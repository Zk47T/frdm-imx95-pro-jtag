# 04 — ARM instruction trace into DRAM: ETM → ETF → ETR, 2 GB, driven over JTAG

**Board:** FRDM-IMX95-PRO, Linux 6.18.20 RAM-booted with `linux.sh` (nokaslr, cpuidle.off=1) and
the `board/mkdtb.sh` DTB: 2 GB no-map at `0x4_0000_0000`, 32 MB no-map at `0xf0000000`.
**Date:** 2026-10-16. **Tags:** `[V]` measured · `[?]` not verified · `[R]` reference manual.

## Summary

- **Core 0's full instruction trace was captured into a 2 GB DRAM buffer:**
  - 1.58 GiB in 8 s, at 212 MB/s;
  - the whole trace path was programmed from the debugger.
- **It decoded with OpenCSD 1.8.3 without a single error:**
  - about 12 instructions per byte of trace;
  - roughly 20 billion instructions over the capture.
- **Four independent checks agree with the decode.** The strongest: the trace counts 3,073
  instructions per 4 KB `clear_user`, and the loop in `vmlinux` executes 3,074.
- **Linux can't drive the trace itself here:** enabling the ETF/ETR in the DT panics the boot (§2).

## 1. The hardware `[R]` `[V]`

| Block | Debugger address (APB-AP) | A55 address | Notes |
|---|---|---|---|
| ETM, core n | `0x80810000 + n·0x100000 + 0x30000` | `0x40840000 + …` | ETMv4.2 (TRCIDR1 0x4100f423, TRCDEVARCH 0x47724a13); 64-bit timestamps, BB, CC, return stack; the OS lock is set at boot |
| funnels (cluster, system) | — | — | fixed, no registers (RM 14.1 note) |
| ETF | `0x81030000` | `0x41030000` | TMC (PIDR 0x961), DEVID 0x480 = ETF, 16 KB |
| ETR | `0x81040000` | `0x41040000` | TMC, DEVID 0x1b40 = ETR; own AXI master, no `iommus` in the DT; wrote above 4 GB without MemErr |
| no TPIU | | | trace leaves only through the ETR (or SWO for ITM) |

The APB-AP sees the RM's `0x41xxxxxx` debug map at `0x81xxxxxx`. That is the same offset found for the A55 debug blocks in [01](01-jtag-bringup.md).

## 2. Why not Linux's CoreSight driver `[V]`

With the `etm`, `funnel`, `funnel-sys`, `etf` and `etr` nodes set to okay (`TRACE=1 board/mkdtb.sh`), the kernel dies 0.2 s into the boot:

```
Internal error: synchronous external abort: 0000000096000010 [#1] SMP   (CPU 4, PID 1)
pc : amba_read_periphid+0xb8   x8 = 0x41031000
  amba_match ← __driver_attach ← ... ← arm_mhu_driver_init
Kernel panic - not syncing: Attempted to kill init!
```

- That is the A55's read of the ETF's PIDR at `0x41030FE0`.
- The same register reads fine from the debugger (`0x81030FE0`: 0x61 0xb9 0x1b 0x00).
- So the A55's path to the CoreSight system block is fenced, probably by the TRDC or a firmware setting. `[?]` which one.
- This panic was found without a console: the log was read over JTAG through a halted core,
  because the panic text was still in the caches ([02](02-linux-without-a-console.md) §3).
- `TRACE=1` stays off by default in `board/mkdtb.sh`. The 2 GB `trace@400000000` region is
  reserved either way, so the debugger-driven trace below can use it.

## 3. How it is driven (`imx95-jtag.tcl`, `ocd.sh trace`)

The kernel's own sequences (`etm4_enable_hw`, `__tmc_etr_enable_hw`, `tmc_flush_and_stop`), written over the APB-AP:

1. **ETR:**
   - CTL=0, wait TMCReady.
   - RSZ = size/4, MODE=0 (circular).
   - AXICTL: non-secure (bit 1), write burst 16.
   - DBALO/DBAHI and RRP/RWP (+HI) = base. FFCR=0x133. CTL=1.
2. **ETF:** MODE=2 (hardware FIFO), FFCR=0x3, CTL=1.
3. **ETM:**
   - TRCOSLAR=0 (the OS lock), TRCPDCR.PU, TRCPRGCTLR=0, wait idle.
   - TRCCONFIGR=0, events off, TRCSYNCPR=0xC, TRCTRACEIDR=0x10.
   - TRCVICTLR=0x201 (EVENT=always, start/stop logic started, **no exception level excluded**: Linux runs at EL2 under VHE here, so the kernel's default EL2 exclusion would hide the kernel).
   - TRCPRGCTLR=1.
4. **Stop:**
   - ETM off, wait idle.
   - ETF FlushMan, then off.
   - ETR FFCR |= FlushMan|StopOnFl, wait.
   - Read STS, RWP, RRP. CTL=0.

```
./ocd.sh etm-ids 0                                   # TRCIDRs for the decoder
./ocd.sh trace 0 0xf1000000 0x1000000 300            # 16 MB below 4 GB, readable over JTAG
./ocd.sh trace 0 0x400000000 0x80000000 8000         # 2 GB above 4 GB
```

Readout:
- **Below 4 GB** the AHB-AP reads the buffer directly (97 KiB/s).
- **Above 4 GB** the AHB-AP can't reach it (32-bit, CFG.LA=0). Linux copies a window down with
  `/mnt/physwin c SRC DST LEN` and checksums it with `physwin s`, then the window is read over JTAG.

Decode: `trace/mksnap.py` builds the OpenCSD snapshot:
- the buffer, rotated at RWP if it wrapped;
- the ETM registers as read;
- `vmlinux`'s executable segments and any `--elf`, space ANY.

It runs `trc_pkt_lister` (built in `elb1/11.tmp/opencsd`) and prints a per-function instruction count.

## 4. Captures

| # | Buffer | Workload on core 0 | ETR result | Decode |
|---|---|---|---|---|
| T1 | 16 MB @ 0xf1000000 | idle | RWP +0x20, not full | — (300/300 PC samples in `cpu_do_idle`: a core in WFI makes no trace) |
| T2 | 16 MB @ 0xf1000000 | `taskset 1 dd if=/dev/zero of=/dev/null bs=4096` | full in <300 ms, no MemErr | 199.7 M instructions, 0 errors |
| T3 | **2 GB @ 0x4_0000_0000** | the same | **1,696,078,752 B in 8.000 s**, not full, no MemErr | two 8 MB windows (start, end): 99.86 M / 99.85 M instructions each, 0 errors |

T2's profile, instructions executed:

```
69.39 %  __arch_clear_user        (read() from /dev/zero zeroes the 4 KB user buffer)
 3.75 %  el0t_64_sync             2.66 %  __fsnotify_parent   2.48 %  vfs_write
 2.30 %  vfs_read                 2.03 %  ret_to_user         1.53 %  rw_verify_area
 ...      dd_main, write_and_stats, full_write, dd_read (busybox, EL0)
PE contexts: EL2N (kernel, VHE) and EL0N (dd); exceptions 90,174 = returns 90,174
```

T3's start and end windows give the same profile to 0.01 %.

## 5. How the result was checked

1. **The bytes:**
   - T2: a second read of a 64 KiB window was identical.
   - T3: the FNV-1a checksums of both windows match in high DRAM, in the low copy, and on the PC.
   - The bytes just past RWP are untouched power-up DRAM, not trace. `[V]`
2. **The code path, instruction by instruction:**
   - `__arch_clear_user`'s loop is `nop; nop; sttr xzr; add; subs; b.hi` (the two nops are alignment padding), 6 instructions per 8 bytes.
   - For 4,096 bytes that is 3 + 2 + 511·6 + 3 = **3,074** instructions.
   - The trace gives **3,073** per record (69.39 % of 4,429 per read/write pair). The first decoded ranges are that loop: `0xffff8000811c0a38–a50, num_i(6)`. `[V]`
3. **Independent PC sampling** (PMPCSR, 100 samples while `dd` ran) ranks the same functions:
   - `__arch_clear_user` 43 %, user 15 %, `ret_to_user` 7 %, `vfs_write` 6 %, `el0t_64_sync` 5 %.
   - The percentages differ because sampling weighs time and the trace counts instructions. `[V]`
4. **`dd`'s own numbers:**
   - 2.2 GB/s, 1.14 M syscalls/s, so T2's 90,174 syscalls span about 79 ms.
   - That makes 2.5 G instructions/s on a 1.8 GHz dual-issue A55, and 212 MB/s of trace, the same rate T3 measured over 8 s. `[V]`

## 6. Limits and next steps

- **Only core 0 has been traced.** Cores 1–5 have their ETMs at +n·0x100000. The ETF/ETR take all of them through the funnels, with a different TRCTRACEIDR each. `[?]`
- **Readout of a full 2 GB:**
  - Over JTAG it would take about 6 hours at 97 KiB/s.
  - Use the network, a USB stick or the eMMC instead (`dd if=/dev/mem` through `physwin r`).
  - Or trace less: address-range filters (TRCVIIECTLR + TRCACVRn) to a few functions, e.g. the DSI/ATTINY probe path.
- **Timestamps and cycle counts:** TRCCONFIGR TS/CCI and the system counter as the time base (RM: no CoreSight TSG, the system counter is used). `[?]`
- **Linux's own driver:** find what fences the A55's view of `0x41030000` (TRDC / SM configuration), then `TRACE=1`. `[?]`
- **When the trace is idle:** a core in WFI produces nothing (T1). Trace a workload, or switch the idle loop off with `cpuidle.off=1`, which only keeps the core powered, not busy.

## 8. All six cores at once (2026-10-16 evening) `[V]`

`cs_multi_start {0 1 2 3 4 5} BASE SIZE CONFIGR` (`imx95-jtag.tcl`) starts the sink once, then each core's ETM with trace ID 0x10 + core. The fixed funnels merge them into the one ETR buffer.
- All six ETMs answered with identical ID registers. The cores must be powered (`cpuidle.off=1`).
- `mksnap.py --cores 0,1,2,3,4,5` writes one cpu/etm pair per core. `tracetool.py` keeps one call stack per trace ID and writes one Perfetto track per core.

Capture T6: 8 MB, TS on (TRCCONFIGR 0x800), 300 ms, one job pinned per core:

| Core | Job | Instructions in the buffer | What the decode shows | Overflows |
|---|---|---|---|---|
| 0 | `dd if=/dev/zero` | 8.4 M | `__arch_clear_user` | 0 |
| 1 | `dd if=/dev/urandom` | 15.5 M | `chacha_permute`, `chacha_block_generic` | 0 |
| 2 | `head -c ... /dev/zero \| md5sum` | 15.1 M | `md5_process_block64`, `getc`/`putchar` | **7** |
| 3 | `head ... \| gzip -1` | 18.1 M | `deflate`, `fill_window`, `crc32` | **7** |
| 4 | a shell loop | 16.7 M | `evaltree`, `evalcommand`, `argstr` (busybox ash) | 0 |
| 5 | idle | 0.13 M | idle entry/exit; 5× TRACE_ON after WFI | 0 |

- **Every core's trace matches the job pinned to it.** In Perfetto (trace processor: 0 import errors), the busy cores' windows overlap at about 0.55–1.2 ms on the shared timestamps. The idle core's part of the buffer reaches 12 ms back, because it writes so little.
- **Five busy cores are too much for the shared path:**
  - The two with the densest user-space branching (cores 2 and 3) lost trace 7 times each (`NO_SYNC`/`TRACE_ON [overflow]`). The decoder resynchronises, and their call stacks start again after each gap.
  - **Fixes:**
    - fewer cores;
    - address filters;
    - **ETM stall** (TRCSTALLCTLR, supported here: TRCIDR3 bit 26). That makes a core wait instead of dropping trace, at the cost of its timing.
- `tracetool.py index --count N` caps each core at N instructions (1 M here: 26 s, ~119 k slices).

## 7. Lessons

1. **Check the decode against the code, not just "no errors".** One loop with a known
   instruction count turns a plausible profile into a measured one.
2. **The debugger sees what the OS can't:** the same ETF register aborts from the A55 and reads cleanly from the APB-AP.
3. **A buffer far larger than the readout path is still useful.** Capture into GBs, then copy and
   decode the windows that matter.
