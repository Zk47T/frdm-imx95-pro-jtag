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
- **All six cores trace into the one buffer and end at the same instant** (§7). On the board's
  real system the trace names the process on every core (§8).
- **Decode against the kernel text as it runs, read from DRAM:** Linux patches 0.4 % of its text
  at boot, and decoding against `vmlinux` loses returns (§8.1).

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
4. **Stop** (`cs_multi_stop`, also for one core):
   - ETR FFCR |= FlushMan|StopOnFl, wait for TMCReady: every core's trace ends here (§7.1).
   - Each ETM off, wait idle.
   - ETF off.
   - Read STS, RWP, RRP. ETR CTL=0.

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

- **All six cores:** done (§7); a real system with process names: §8.
- **Readout of a full 2 GB:**
  - Over JTAG it would take about 6 hours at 97 KiB/s.
  - Use the network, a USB stick or the eMMC instead (`dd if=/dev/mem` through `physwin r`).
  - Or trace less: address-range filters (TRCVIIECTLR + TRCACVRn) to a few functions, e.g. the DSI/ATTINY probe path.
- **Timestamps and cycle counts:** done ([08](08-trace-visualisation.md) §3).
- **Linux's own driver:** find what fences the A55's view of `0x41030000` (TRDC / SM configuration), then `TRACE=1`. `[?]`
- **When the trace is idle:** a core in WFI produces nothing (T1). Trace a workload, or switch the idle loop off with `cpuidle.off=1`, which only keeps the core powered, not busy.

## 7. All six cores at once (2026-10-16 evening) `[V]`

`cs_multi_start {0 1 2 3 4 5} BASE SIZE CONFIGR` (`imx95-jtag.tcl`, or `ocd.sh trace-all`) starts the sink once, then each core's ETM with trace ID 0x10 + core. The fixed funnels merge them into the one ETR buffer.
- All six ETMs answered with identical ID registers. The cores must be powered (`cpuidle.off=1`).
- `mksnap.py --cores 0,1,2,3,4,5` writes one cpu/etm pair per core. `tracetool.py` keeps the call stacks per trace ID and writes one Perfetto track per core.

### 7.1 Stop the sink first, not the ETMs

The first six-core capture (T6) stopped the ETMs one by one, then the sink. Each core's trace ended at a different time:

| T6, ETMs stopped first | core 0 | core 1 | core 2 | core 3 | core 4 |
|---|---|---|---|---|---|
| trace ends at (ms from the oldest byte) | 3.888 | 5.747 | 7.656 | 9.596 | 11.548 |

- **Why:** each `cs_etm_stop` (a write, then polling TRCSTATR) takes **1.74–1.86 ms** over the CH347F (measured). The cores still running kept filling the ring after the first had stopped, so in Perfetto the cores covered different intervals.
- **Now `cs_multi_stop` stops the ETR first:** FFCR FlushMan|StopOnFl. The flush runs up through the ETF and funnels to every ETM, and the ETR stops writing when it completes. Then the ETMs, then the ETF.
- **Checked before relying on it:** with the ETR stopped, the ETMs ran on for 40 s. RWP did not move, and every ETM still reached idle (TRCSTATR 0x3). A stopped ETR drops trace without stalling the ATB.
- **T7 (same jobs, sink first):** every core's trace ends at **7.356 ms**, the same timestamp.
- The start is still one core after another, milliseconds apart. A ring that has wrapped doesn't show it.

### 7.2 Captures

T7: 8 MB, TS on (TRCCONFIGR 0x800), 300 ms, one job pinned per core. Decoded against the live kernel text (§8.1):

| Core | Job | Instructions in the buffer | What the decode shows | Overflows |
|---|---|---|---|---|
| 0 | `dd if=/dev/zero` | 18.5 M | `__arch_clear_user` | 0 |
| 1 | `dd if=/dev/urandom` | 22.1 M | `chacha_permute`, `chacha_block_generic` | 0 |
| 2 | `head -c ... /dev/zero \| md5sum` | 15.6 M | `md5_process_block64`, `getc`/`putchar` | **24** |
| 3 | `head ... \| gzip -1` | 14.7 M | `deflate`, `fill_window`, `crc32` | **22** |
| 4 | a shell loop | 11.1 M | `evaltree`, `evalcommand`, `argstr` (busybox ash) | 0 |
| 5 | idle | 0.09 M | idle entry and exit, around WFI | 0 |

- **Every core's trace matches the job pinned to it,** over the same 7.36 ms. Perfetto's trace processor reports 0 import errors.
- **T8, six busy cores** (core 5: `head | sha256sum`): all six end at 6.607 ms. 8 MB in 6.6 ms is 1.27 GB/s through the one ETR.
- **The shared path can't carry that much:**
  - The cores with the densest user-space branching lose trace. In T7, cores 2 and 3 overflow 24 and 22 times in 7.4 ms. In T8, cores 2, 3 and 5 overflow about 1,000 times each in 6.6 ms, while core 4 (the shell) has 0.
  - The decoder resynchronises, and that core's call stack starts again after each gap.
  - **Fixes:**
    - fewer cores;
    - address filters;
    - **ETM stall** (TRCSTALLCTLR, supported here: TRCIDR3 bit 26). That makes a core wait instead of dropping trace, at the cost of its timing.
- These are synthetic loops. They look regular in Perfetto because they are: the same few calls a million times. §8 is a real system.

## 8. A real system (T9, 2026-10-16) `[V]`

**The workload is the board's own image** ("fast-boot eglfs": systemd, journald, udevd, networkd, dbus). `board/scenario-real.sh` runs its own tools, with nothing pinned:
- `find /usr | xargs sha256sum` after dropping the page cache;
- `journalctl -b`;
- `udevadm trigger --action=change` + `settle`;
- `systemctl daemon-reload`;
- `logger` messages into journald.

It also collects a PID → name map from `/proc/*/comm` every 0.2 s, and `/proc/interrupts` before and after.

```
board:  sh scenario-real.sh
PC:     JTAG_KHZ=30000 ./ocd.sh trace-all 0xf1000000 0x1000000 1500 0x8880
board:  sh scenario-real.sh done           # then copy /tmp/pidmap.txt to the PC
PC:     ./ocd.sh read 0xf1000000 0x1000000 trace/etr-t9.bin
        ./ocd.sh ktext                      # once per kernel build (§8.1)
        trace/mksnap.py ... --configr 0x8880 --cores 0,1,2,3,4,5 --pids pidmap.txt --kernel-live trace/ktext-live-*.bin
        trace/tracetool.py index ss-t9-real; trace/tracetool.py perfetto ss-t9-real
```

**The process on every core:**
- TRCCONFIGR 0x8880 = TS + VMID + VMIDOPT. With VMIDOPT the VMID field carries CONTEXTIDR_EL2.
- This kernel runs at EL2 (VHE) with `CONFIG_PID_IN_CONTEXTIDR=y`, so CONTEXTIDR_EL2 holds the running task's PID. This is Linux's `contextid2` option.
- OpenCSD prints it as `PE_CONTEXT(... VMID=0x425)`. The PID changes inside `__switch_to`.

**What the 16 MB holds, the last 50.7 ms** (`tracetool.py index`, time per task):

| Core | Tasks | Notes |
|---|---|---|
| 0 | idle 99 %, `kworker/0:1H-mmc_complete` 1 %, `kworker/0:2-events` | all the eMMC interrupts (`sdhci_irq`, 56) and their completion work |
| 1 | idle 100 %, `rcu_preempt` | asleep from 20 ms to the end |
| 2 | idle 100 %, `rtt-console` | the kit's JTAG console polling |
| 3 | `sha256sum` 98 %, idle 2 % | its own code between `read()` syscalls every ~80 µs; blocks on the eMMC at 13 and 39 ms |
| 4 | `udevadm` 100 % | `openat`/`write` on sysfs `uevent` files, 31 M kernel instructions |
| 5 | — | no trace at all: asleep for the whole 50.7 ms |

**Checked against the board's own counters:**
- `/proc/interrupts` has `mmc0` only on CPU0, and the trace has `sdhci_irq` only on core 0.
- The trace has about 12 timer interrupts per core in 50.7 ms, which matches `CONFIG_HZ=250`.
- Core 5 traced alone for 200 ms on the idle board wrote 0x20 bytes. Its ETM was programmed and powered (TRCPDSR.STICKYPD 0), and the core was just in WFI. With `CONFIG_NO_HZ_IDLE` an idle core takes no tick, so it writes nothing.

### 8.1 Decode against the kernel as it runs

- **Linux rewrites its own text at boot:** alternatives (LSE atomics instead of LL/SC loops), static keys, jump labels.
  - The kernel text read from DRAM (`ocd.sh ktext`: physical 0x90400000, 18.9 MB, 69 s over SWD) differs from `vmlinux` in **19,805 words (0.42 %), in 7,018 functions**. Most are branches turned into NOPs.
  - Two reads 25 minutes apart are identical.
- **Decoded against `vmlinux`, the decoder follows branches that no longer exist.**
  - At `_raw_read_lock` it took a conditional branch, and the next address in the trace was the caller, 14 MB away.
  - Returns were lost in `_raw_read_lock`, `kmem_cache_alloc_noprof` and `percpu_counter_add_batch`, and call stacks grew to **597** deep.
  - With `mksnap.py --kernel-live` the deepest stack is **25–47**, and 4 % more instructions decode.
  - `perf` uses `/proc/kcore` for the same reason.
- **`__arch_clear_user` has no patched word.** That is why T2's instruction count matched `vmlinux` exactly (§5). T6–T8 are now decoded against the live text too.

### 8.2 What a real system adds to the decode (`tracetool.py`)

| In the trace | What it means | How it is shown |
|---|---|---|
| `ADDR_NACC` at a user address (1.19 M in T9) | user programs are dynamically linked and loaded at random addresses (ASLR); there is no image to follow | one slice per stretch, `sha256sum [1056] (user code)`; the kernel nests under it at each syscall or interrupt |
| `TRACE_ON [begin or filter]` | the ETM pauses while the core sleeps in WFI; **not** lost trace | `[WFI: core asleep]` under the idle loop; the call stack is kept |
| a new VMID (PID) | a task switch | one call stack per task, saved and restored (one shared stack piled each task's frames onto the last); a per-core **tasks** track in Perfetto |
| no trace after the idle task | asleep until the capture stopped | the idle slice runs to the capture's end |

- **Time:** a sleep or a stretch of user code has no instructions to interpolate over. So the next timestamp gives that row the time between the two timestamps, minus what the counted kernel instructions need at 1.8 per ns (IPC 1 at 1.8 GHz).
- **Not decoded yet:** the user programs' own functions. That needs each process's mappings (`/proc/PID/maps`) and its libraries as images, per PID.

### 8.3 Six seconds of the whole system, out over USB (T10, 2026-10-17) `[V]`

**The capture:**
- The 2 GB buffer at 0x4_0000_0000, with TS and PID (TRCCONFIGR 0x8880).
- The scenario started 2.7 s after tracing began, so all of it is inside.
- **6.07 s of all six cores, 903,521,344 bytes;** the buffer didn't wrap.

**The readout goes through the board's own USB-C, not the debug probe:**
- `board/usbexport.sh 0x400000000 LEN` copies the region into `/tmp` (RAM) with `physwin`: 17.8 s, 48 MB/s with uncached reads.
- It then shows the copy as a read-only USB disk: a configfs mass-storage gadget on the dwc3, `4c100000.usb`.
- `trace/usbget.sh` reads it: **862 MB in 20.8 s, 41 MiB/s**. The link came up as USB 2.0 high speed (480 Mb/s); the controller does USB 3.
- The SHA-256 is the same on the board and the PC.
- Over the probe at 266 KiB/s it would take 55 minutes.

**Processing:** this is too big to index (billions of rows), so there are two new commands:
- `mksnap.py --no-decode` builds the snapshot without the single-process full decode.
- `tracetool.py summary` cuts the buffer into 16 MB chunks. Each chunk × source is decoded in its own process and reduced at once to time per task, instructions, overflows and folded stacks.
  - T10: 54 chunks × 6 sources = 324 jobs, 8 minutes on 12 processes, 4.4 GB of RAM.
  - Checked on T9, cut into 4 MB chunks: the same time per task as the full index, and instructions within 0.5 %. The difference is what each chunk edge loses until the decoder's next sync point.

| Core | Instructions | Overflows | Time per task (6.07 s) |
|---|---|---|---|
| 0 | 339 M | 7,250 | idle 83 %, `systemd-udevd` 5 %, `sha256sum` 3 %, `find` 2 %, the eMMC kworker 1 % |
| 1 | 217 M | 12,080 | idle 88 %, `sha256sum` 2 %, udev workers |
| 2 | 324 M | 9,912 | idle 76 %, `sha256sum` 9 %, `systemd-udevd`, `systemd`, `kworker/2:3+rcu_gp` |
| 3 | 307 M | 11,666 | idle 83 %, `sha256sum` 4 %, `sh`, udev workers |
| 4 | 602 M | 17,734 | idle 74 %, `udevadm` 11 %, `sh` 2 %, udev workers |
| 5 | 265 M | 7,696 | idle 61 %, `sha256sum` 31 % (three PIDs), `systemd-udevd`, `systemd` |

- **The whole capture as a timeline:** `ss-t10-real-6s/tasks.perfetto-trace` (0.9 MB) holds 22,292 task runs on six tracks (Perfetto: 0 import errors).
  - Idle until 2.7 s; the scenario from 2.9 s.
  - The udev-worker storm at 5.2–5.7 s: 70–84 different tasks per 100 ms, about 90 % of all six cores busy.
- **Detail anywhere in it:**
  ```
  tracetool.py cut ss-t10-real-6s ss-t10-storm --ms 5300 5320
  tracetool.py index ss-t10-storm
  tracetool.py perfetto ss-t10-storm --window 5300 5320
  ```
  - The cut is two chunks (32 MB) with the parent's time axis (`timebase.txt`); it produced 893,716 slices.
  - In those 20 ms: `sha256sum` on core 0; a dozen udev workers and `systemd-udevd` switching on cores 1–4; short-lived children on core 5. Those are PIDs 1023–1029, gone before the 0.2 s name collector saw them, so they show as `?`.
- **The overflows are real** (`TRACE_ON [overflow]`), 7,000–17,700 per core, all in the busy phases.
  - In one 27 ms chunk at 620 MB/s, cores 1 and 2 lost trace 231 and 276 times.
  - Bursts from several cores running dense code at once overrun the shared path (a 16 KB ETF FIFO, one ETR).
  - The task timeline is unaffected: PIDs and timestamps keep coming. The instruction-level detail has holes.

**All of it in one command:**
```
SUDO_PASS=... trace/capture.sh NAME SECS ['board command']
```
1. It traces SECS s of all six A55s, kernel only, while the command runs on the board. The default command is `scenario-real.sh`'s workload, and a PID collector runs alongside.
2. It reads the buffer out over USB, checks the SHA-256 on both sides, builds the snapshot against the newest live kernel text, and runs `tracetool.py summary`.
3. The result is `trace/ss-NAME/` with `summary.json`, `tasks.perfetto-trace` and `trace.folded`.
4. A test: 4 s around a `find | md5sum` and `journalctl` came to 13 MB, 2,123 task switches and 0 overflows.

**The fix (T11): don't trace user code.** TRCVICTLR = 0x100201 excludes non-secure EL0. T11 ran the same scenario for the same 6 s:

| | T10: everything traced | T11: user code excluded |
|---|---|---|
| trace for 6.07 s | 903 MB | **449 MB** |
| kernel instructions decoded | 2.05 billion | 2.14 billion |
| overflows | 76,338 | **0** |
| task switches | 22,292 | 21,028 |

- **User time per process is still measured,** from the timestamps on both sides of each stretch.
- **What the trace shows at a return to user space:** `eret`, `PE_CONTEXT EL0`, `EXCEPTION_RET`, a timestamp. Then, at the next kernel entry: `TRACE_ON [begin or filter]`, `PE_CONTEXT EL2`, and the EL0 vector (`0x...10c00`) with no EXCEPTION element. `tracetool.py` turns that into a user-code row for the process, with the kernel nested under it.
- **The detail is clean:** a 20 ms cut of the busiest stretch (`ss-t11-storm`, 5500–5520 ms) has stacks 47–52 deep at most, 656,142 slices, and 0 Perfetto import errors.
- **`ocd.sh trace-all` now excludes user code by default.** `VICTLR=0x201` traces it too, for a static program whose ELF goes to `mksnap.py --elf`.
- **The image-less kernel code is seccomp:** `__seccomp_filter` calls JIT-compiled BPF filters just past the kernel image (after `kimage_limit`). systemd and the udev workers run sandboxed, and every one of their syscalls runs a filter. 11,354 such calls in one 16 MB chunk of T11.
- **Short-lived processes stay `?`:** this kernel has no ftrace (`CONFIG_FTRACE` off), and they come and go between the 0.2 s `/proc` samples.

### 8.4 One driver for 20 s: address-range filters (T13, 2026-10-17) `[V]`

**Each A55 ETM has 4 address-comparator pairs** (TRCIDR4.NUMACPAIRS). With an include range it records only instructions inside it:
- `RANGES="LO:HI ..."` on `ocd.sh trace-all` or `trace/capture.sh` sets TRCACVR 2n and 2n+1, TRCACATR = 0 (instruction address, every EL) and INCLUDE bit n of TRCVIIECTLR (`cs_ranges` in `imx95-jtag.tcl`).
- `mksnap.py --ranges` notes the ranges in the snapshot (`ranges.txt`). `tracetool.py` then reads every pause in the trace as time outside them: no task, the stack starts again, and it isn't drawn in Perfetto.

**The MMC core and SDHCI host drivers are one block of the kernel image:** 1,007 functions, 285 KB, from `mmc_cqe_request_done` (`0xffff800080cdd6a0`) to `0xffff800080d244c0`. The objects in `drivers/mmc/` were matched against System.map.
```
SUDO_PASS=... RANGES="0xffff800080cdd6a0:0xffff800080d244bc" trace/capture.sh t13-mmc-20s 20
```
- **20 s of the real scenario came to 9.9 MB of trace.** T11 needed 449 MB for 6 s of the whole kernel. The MMC code ran for 7.0 s of the 20.
- **It indexes whole:** 10 s and 1 GB of RAM, 1.32 M Perfetto slices, 0 import errors, stacks 5–8 deep.
- **Where the eMMC driver's instructions go:** `sdhci_irq` 13 %, `sdhci_prepare_dma` 7 %, `sdhci_send_command` 7 %, `__sdhci_set_timeout` 6 %, and the i.MX eSDHC register accessors (`esdhc_writew_le`, `esdhc_readl_le`, `esdhc_writel_le`) 15 % together. Then the `mmc_blk` request path: `mmc_blk_data_prep`, `mmc_mq_queue_rq`, `mmc_blk_rw_rq_prep`, `__mmc_claim_host`.
- **Who runs it, as a share of each core's time in the driver:**
  - On core 0, the idle task holds 51 %: the eMMC interrupt is routed to CPU0 only, so its handler runs there. The completion kworker holds 28 %.
  - On the other cores it's `sha256sum` and `find`, submitting their reads through blk-mq.
- **A trap found on the way:**
  - `summary`'s chunks first lacked `ranges.txt`, so the filtered trace was read as an unfiltered one. Its call stacks never unwound, and every task switch reopened them all: 12.8 M rows from one 2 MB chunk, and 4 GB per worker.
  - Chunks and cuts now carry it, and the stack is capped at 256 levels: the real maximum is 52.

## 9. Lessons

1. **Check the decode against the code, not just "no errors".** One loop with a known
   instruction count turns a plausible profile into a measured one.
2. **The debugger sees what the OS can't:** the same ETF register aborts from the A55 and reads cleanly from the APB-AP.
3. **A buffer far larger than the readout path is still useful.** Capture into GBs, then copy and
   decode the windows that matter.
4. **Stop a shared trace at the sink.** Stopping the sources one at a time over a slow probe
   gives each core a different end.
5. **Decode against the memory as it is, not the file it came from.** Self-patching code
   (alternatives, static keys) is invisible until the call stacks drift.
6. **Read a big capture out through the board's own USB, not the debug port:** 41 MiB/s against 266 KiB/s.
   Then summarise all of it and cut out windows for detail; nothing opens 6 s of six cores whole.
7. **Trace a real system before drawing conclusions from a picture.** Pinned synthetic loops
   look synthetic: idle cores, task switches, interrupts and user code without images are what a
   real trace is made of.
