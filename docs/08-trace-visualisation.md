# 08 — Seeing the trace: timelines, flame graphs, and going back and forth

**Tags:** `[V]` measured 2026-10-16 · `[?]` not verified.

## 1. What a trace contains, and what it doesn't

- **The A55's ETM records control flow:** which instructions ran, in order, and the exceptions.
  - It has **no data trace** (TRCIDR0.TRCDATA = 0), so register and memory *values* are not recorded.
  - It can add **timestamps** (TRCCONFIGR.TS) and **cycle counts** (TRCCONFIGR.CCI). Both work here (§3).
- **"In RAM" doesn't limit going back and forth.** Once captured and decoded, the trace is the complete history of that window. You can move through it in either direction as often as you like. That's what §4 does.
- **What you can't do:**
  - run the *live* CPU backwards;
  - see a variable's value at a past instruction. TRACE32's CTS reconstructs those when there is a data trace, or by re-simulating from known state; the A55 gives neither for free.
- **Where the history lives doesn't matter to the analysis.** In DRAM through the ETR (this chip has no trace port) or in a probe's trace memory, it's the same record. On the i.MX95 every tool, TRACE32 included, reads the ETR buffer.

## 2. The pipeline

```
ocd.sh trace / cs_trace_start(..., configr, ccctlr)   ETM -> ETF -> ETR -> DRAM
  -> readout: AHB-AP over SWD 5 MHz memaccess 2 (276 KiB/s), or physwin + JTAG above 4 GB
trace/mksnap.py      snapshot (buffer rotated at RWP, ETM registers, vmlinux + ELF images) + OpenCSD decode
trace/tracetool.py index      call stack rebuilt from the decoded control flow  -> index.npz
                   perfetto   nested function slices on a real time axis       -> trace.perfetto.json
                   speedscope the same as an evented profile                   -> trace.speedscope.json
                   folded     stacks weighted by CPU cycles (or instructions)  -> trace.folded -> flamegraph.svg
                   nav        step forward/back, with source, disassembly, call stack
```

**Rebuilding the call stack:**
- OpenCSD says how each executed range ended: `b+link` (BL/BLR) is a call, `A64:ret` a return, anything else a jump.
- A call pushes and a return pops. A jump into another function replaces the top, because it's a tail call.
- An exception pushes a marker and the vector code; the exception return pops back to the marker.
- **Checked against the known path** (16 levels, user → syscall → kernel):

```
dd_main → dd_read → safe_read → read            (busybox, EL0)
  → el0t_64_sync → el0t_64_sync_handler → el0_svc → do_el0_svc → el0_svc_common
  → invoke_syscall → __arm64_sys_read → ksys_read → vfs_read → read_zero → __arch_clear_user   (EL2)
```

- Frames from before the trace started are unknown, so returns above the first frame stay at depth 1.

## 3. The time axis `[V]`

| Capture | TRCCONFIGR | Trace density | Time base |
|---|---|---|---|
| T2/T3 | 0 | ~12 instructions per byte | instruction count only (estimated 2.5 G instr/s) |
| T4 | 0x800 (TS) | 11.4 per byte; a timestamp per exception (~1 per 1,000 instructions) | **timestamps**: the 24 MHz system counter with 8 fractional bits, **6.144 G ticks/s** (measured 6.137 against `dd`'s own syscall rate) |
| T5 | 0x810 (TS + CCI), TRCCCCTLR 4 | **3.3 per byte**; a cycle count every ~10 instructions | **CPU cycles**: 9,972,416 cycles over 5.518 ms of timestamps = **1.8073 GHz** (the SM's armpll: 1.8 GHz) |

**T5 checks:**
- `__arch_clear_user` takes a median **1,554 cycles for 3,074 instructions**, IPC 1.98.
  - Its 6-instruction loop dual-issues in 3 cycles: 512 × 3 + ~18 = 1,554.
  - That's 0.860 µs per 4 KB.
- Other functions: `read_zero` 15 ns (IPC 1.00), `vfs_read` 16 ns, `ret_to_user` 35 ns.
- Timestamps alone, interpolated by instruction count, gave impossible numbers for short functions near an anchor (`el0t_64_sync` "25 G instr/s"). Cycle counts fix that.
- **For timing work, capture with TS+CCI.** For the longest window, capture with neither.

## 4. Viewing and moving through it

| View | Open with | Shows |
|---|---|---|
| `trace.perfetto.json` | https://ui.perfetto.dev (the file stays in the browser) | a timeline of nested function slices; zoom to single calls; durations; args = instructions and first instruction index |
| `trace.speedscope.json` | https://www.speedscope.app (local) | time order, left-heavy (merged) and sandwich (callers/callees of one function) |
| `flamegraph.svg` | any browser | where the cycles go, by stack |
| `tracetool.py nav SNAP [N]` | terminal | the history at instruction level |

`nav` commands:

| Command | Does |
|---|---|
| `s` / `b` [N] | step forward / back N instructions |
| `n` / `p` | next / previous range |
| `f` / `F` | forward to the next call or return / back to before this function was called (reverse-finish) |
| `find` / `rfind NAME` | next / previous entry into a function |
| `g N` | go to instruction N |
| `bt` | the call stack at this point, with the instruction where each frame was entered |

Each step prints the instruction index, the time, the EL, function+offset, the disassembly, and the
source line from the kernel tree (addr2line on the DWARF `vmlinux`).

```
#4880   t=1.952 us  EL2  read_zero+0x0      paciasp
        drivers/char/mem.c:479   {
(trace) bt
  #0  read_zero              entered at instruction #4880
  #1  vfs_read               entered at instruction #4794
  ...
  #13 busybox_unstripped:dd_main   entered at instruction #97
```

## 5. What TRACE32 adds, and what to build next

TRACE32 is ahead in **analysis software**, not in access: on this chip it reads the same ETR buffer.

| TRACE32 feature | Here | To build |
|---|---|---|
| trace listing with source, back and forth | `tracetool nav` (text) | a GUI pane synced with the timeline |
| function timing, nesting, statistics | Perfetto, speedscope, folded/flame graph | per-function min/max/avg tables (from `index.npz`) |
| CTS: registers and variables at past points | — (no data trace) | re-execute from a snapshot: halt at the start (registers + memory windows), then emulate (e.g. Unicorn) along the traced path, checking every branch against the trace |
| multicore time correlation | core 0 only | ETMs of cores 1–5 with their own trace IDs; one Perfetto track per core, aligned on the common timestamps |
| code coverage | — | mark every executed range against `vmlinux`'s functions/lines |
| OS awareness (tasks) | — | Context ID tracing (TRCCONFIGR.CID) gives the PID per range; map it to `comm` |
| triggers, filters | — | address-range include/exclude (TRCVIIECTLR + TRCACVRn), start/stop on an address |

**Practical order:**
1. CID tracing and per-process tracks.
2. All six cores.
3. Address filters to record one subsystem across a boot (e.g. the DSI/ATTINY probe).
4. Then the emulation-based register view, if it's still wanted.

## 6. Lessons

1. **Check the decoder's output against the hardware.** The instruction count matched the loop
   exactly, and the cycle count matched both the dual-issue and the PLL. Only then are the pictures worth looking at.
2. **Timestamps give absolute time; cycle counts give the shape.** Interpolating timestamps by instructions misleads at short scales.
3. **The expensive part of TRACE32 is reproducible in pieces:** Perfetto and speedscope already are good viewers. What they lack is the instruction/source pane and data reconstruction.
