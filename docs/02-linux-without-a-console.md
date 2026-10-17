# 02 — Running and inspecting Linux without flashing, without network, without UART

**Tags:** `[V]` measured 2026-10-16 · `[?]` not verified.

What this kit adds so the Pro can be debugged with only the CH347F and USB, and later the debug UART too.

## 1. Boot without touching the eMMC `[V]`

```
SW4 = serial download, power on            -> lsusb 1fc9:015d (ROM SDP)
scripts/ramboot.lst via uuu                 -> U-Boot from RAM, fastboot gadget 1fc9:0152
./linux.sh                                  -> Image + board/out/*-jtag.dtb + board/out/initramfs.cpio.gz, booti
```

- The kernel is the deployed DSI build (6.18.20), with the bootargs of the falcon DTB plus `nokaslr cpuidle.off=1`:
  - `nokaslr` makes the link address the run address, for symbol reads and trace decode;
  - `cpuidle.off=1` keeps the cores powered, so their debug and ETM registers stay reachable.
- `VERBOSE=1` prints every kernel message on the A55 UART.
- The **initramfs**:
  - mounts the eMMC root and mounts a tmpfs on its `/mnt`;
  - copies static `busybox`, `rtt-console`, `physwin`, `jtagsh` and `i2cbang.sh` there;
  - starts the JTAG shell, then `switch_root`s to the eMMC root's own `/sbin/init` (ui-init → cluster → systemd).
- **The eMMC is not written,** except for what the image's own init writes on every boot (ui-init's trial marker).
- `board/mkdtb.sh` builds the DTB from the deployed `rpi-7inch-panel` DTB:
  - LPUART5 off, because its pins are the JTAG pins;
  - 32 MB no-map at `0xf0000000` (RTT block, JTAG window) and 2 GB no-map at `0x4_0000_0000` (trace);
  - optional `TRACE=1` (Linux CoreSight, which panics: [04](04-arm-trace-into-dram.md) §2) and `I2CGPIO=1` ([03](03-dsi-and-the-i2c-fault.md)).

## 2. A shell over JTAG alone `[V]`, with an open problem (§4)

- **Board side:** `rtt-console` (the T113 one, opening its own `dev/mem`) keeps a SEGGER RTT block at `0xf0000000`. `jtagsh` waits for the handover, then chroots the shell into the real root, so `/dev`, `/dev/i2c-*` and `/usr/bin` all work.
- **Host side:**
  - `console.sh start` runs one OpenOCD (the build with the mem_ap RTT commands, `elb1/11.tmp/ocd-fast-build`) that polls the block through the AHB-AP and serves the shell on `:9090`.
  - `jsh.py 'CMD'` runs one command.
  - `ocdrpc.py` talks to the same OpenOCD's Tcl port (`:6666`).
- **First boot:** the shell was up 1 s after `console.sh start` and answered `dmesg`, debugfs and so on at 26 s of uptime.

## 3. The kernel log and the PCs, read from outside `[V]`

`dmesg.py` reads printk's ring buffer through the AHB-AP:
- It is the T113's `jtag-dmesg.py` ported to 64 bits: 8-byte ids, state in bits 63:62.
- Kernel image addresses: phys = va − `_text` + `0x90400000`, checked with `linux_banner`.
- `-c CORE` reads through a halted core instead (its caches and MMU).

**It found the panic of [04](04-arm-trace-into-dram.md) §2:**
1. The AHB-AP view stopped at 0.193 s, because the panic's lines were still in the caches.
2. PC sampling showed five cores in `local_cpu_stop` and one in `__delay`: a panic.
3. `dmesg.py -c 4` then read the whole report, `Internal error: synchronous external abort … amba_read_periphid`.

**PC sampling without halting:**
- The A55 is ARMv8.2, so the sample is PMPCSR in the PMU block (debug base + 0x20000, offsets 0x200/0x204); EDPCSR is not implemented (EDDEVID.PCSample = 0).
- It gave the panic location, and later the `dd` profile that cross-checks the trace.

### 3.1 A whole-system `top` through JTAG alone (2026-10-17) `[V]`

**The PMU's sample block also captures the context IDs, at the same instant as the PC.** Read at +0x220, measured with a busy loop pinned to core 2:

| Offset | Register | Here |
|---|---|---|
| 0x220 / 0x224 | PMPCSR | a read of 0x220 takes a new sample; 0x224 bit 31 = NS, bits 30:29 = the EL |
| 0x228 | PMCID1SR, CONTEXTIDR_EL1 | 0 (VHE) |
| 0x22c | **PMCID2SR, CONTEXTIDR_EL2** | **the PID** (`CONFIG_PID_IN_CONTEXTIDR`): 0x8cb = 2251, the busy loop |

**`ocd.sh top [SECS]`:**
- One 4-word read per core, round-robin over the six A55s (`a55_top_samples`), then `trace/jtagtop.py`: the busy share and tasks per core, then the busiest tasks and functions.
- Nothing runs on the board and no core stops. Any length works: no trace buffer, no readout.
- About **100 samples per second per core** at 30 MHz JTAG.
- **Names:**
  - `TOP_TTY=/dev/ttyACM2` asks the console for `/proc/PID/comm` of the PIDs still alive, 40 a command: one 1.4 KB command line came back empty.
  - `jtagtop.py --pids` takes the scenario's PID map, which also has the short-lived processes.
  - `TOP_ELF=` names user code of a static program.

**Checks:**
- **A busy loop pinned to core 2:** 507 of 507 samples on core 2 were PID 2251, in BusyBox ash's own functions (`evalcommand`, `expandarg`, `evaltree`). The other cores were idle, apart from the RTT console on core 0.
- **The real scenario** ([04](04-arm-trace-into-dram.md) §8), 6 s, 3,636 samples:

```
core  busy   tasks (share of all samples on that core)
   0  21.6 %  systemd [1] 7.3 %, sha256sum 3.5 %, kworker/0:1H-mmc_complete 2.3 %, dbus-daemon 0.7 %
   1  25.6 %  sha256sum 10.2 %, systemd-udevd 4.6 %, systemd [1] 1.3 %, sh, (udev-worker)
   2  20.1 %  sh 1.7 %, sha256sum 1.5 %, (udev-worker) 1.0 %, ...
   3  40.1 %  sha256sum [2276] 20.6 %, sh 2.3 %, systemd [1] 1.3 %, dbus-daemon, systemd-udevd
   4  27.7 %  udevadm 9.6 %, sha256sum 2.3 %, systemd-udevd, (udev-worker)
   5  23.9 %  sha256sum [2309] 8.1 %, sha256sum 1.5 %, find 1.3 %, (udev-worker)
```

The same picture as the trace's time per task (T10, T11), from a different mechanism.

**Trace or sampling:**

| | The trace | Sampling |
|---|---|---|
| Gives | every instruction and call stack, in a window | where the time goes, statistically |
| Needs | a buffer, a readout and a decode | none of those |
| Fits | an exact path | a quick "what is this board doing" |

### 3.2 Breakpoints and watchpoints in the running kernel (2026-10-17) `[V]`

```
openocd -s openocd -f board/nxp_frdm-imx95-pro.cfg -c init -c imx95_fast -c imx95_a55
gdb-multiarch vmlinux -ex 'target extended-remote :3333' -x gdb/imx95-linux.gdb
```

**`gdb/imx95-linux.gdb`:**
- `tasks` and `current [CORE]` give the task on each A55. arm64 keeps the current task per CPU in `__entry_task`, which gdb reads through `__per_cpu_offset[]`.
- `whowrote SYMBOL` sets a hardware watchpoint, then prints the backtrace and the writing task.
- **The offsets are this build's:**
  - This kernel's debug info has no `struct task_struct` (`CONFIG_DEBUG_INFO_REDUCED`), and the build has no BTF, so the kernel's own `lx-ps` helpers can't work.
  - `pid` = `TSK_STACK_CANARY` − 8 = 1176, from `asm-offsets.h`.
  - `comm` = 1664, where `init_task` holds `swapper/0`.
  - `tasks` checks them: every idle core shows PID 0, `swapper/N`.

**A watchpoint:** `whowrote vm_dirty_ratio` while the board's shell ran `echo 25 > /proc/sys/vm/dirty_ratio`.
```
Thread 4 "imx95.a55.3" hit Hardware watchpoint 1: vm_dirty_ratio
Old value = 20
New value = 25
#0  do_proc_dointvec_minmax_conv ... kernel/sysctl.c:735
#1  __do_proc_dointvec   #3 proc_dointvec_minmax   #4 dirty_ratio_handler
#5  proc_sys_call_handler   #6 proc_sys_write   #8 vfs_write   #9 ksys_write ...
core 3: pid 750 (sh)          <- the console's shell: its 'echo $$' printed 750
```

**A breakpoint:** `hbreak sdhci_irq` while the board read a file after dropping the page cache.
- It stopped core 0, the only CPU that takes the eMMC interrupt.
- The path was `call_on_irq_stack` → `gic_handle_irq` → `handle_fasteoi_irq` → `__handle_irq_event_percpu` → `sdhci_irq`.
- It interrupted PID 76, `kworker/0:1H`, the eMMC completion worker.

**Limits:**
- Every stop halts all six cores (one SMP group), and with them nearly every WAKEUPMIX peripheral ([03](03-dsi-and-the-i2c-fault.md) §5). Keep stops short: RCU warns after about 21 s.
- Kernel text is read-only, so breakpoints are hardware ones (`hbreak`): 6 per core, and 4 watchpoints.

## 4. Open: the board hangs while the JTAG console is in use `[V]` `[?]`

**Seen once:**
- About 1–2 minutes after the JTAG shell came up, with the RTT polling running and a few `jsh.py` commands sent:
  - the A55 UART stopped echoing;
  - **every** AP access over JTAG returned a sticky error (`0xf0000030`), the MDM-AP included, while the TAP and DP still answered.
- The SM (`cpu.r`, `power.r`) showed all A55 cores in RUN, no low-power transition, and no errors in its log.
- The SM `reset` recovered it.

**Not seen:**
- in the next boot, with no JTAG traffic for 4 minutes (the UART shell answered at 255 s of uptime);
- in a later boot with one-shot OpenOCD runs (trace captures, reads) while Linux ran.

**Suspects, untested:**
- the RTT polling's AHB-AP reads (every 100 ms, CSW 0x43800042) racing the A55's Device-nGnRnE mapping of the same page;
- a `jsh.py` command;
- something unrelated.

**To narrow it:**
- RTT polling alone for 10 minutes, no commands;
- then commands alone over the UART with OpenOCD polling;
- then a longer RTT polling interval.

## 5. The UART and the System Manager `[V]`

**USB serial devices on this PC when the CH347F is plugged in first:**

| tty | | |
|---|---|---|
| ttyACM0, ttyACM1 | the CH347F's own UARTs | not the board |
| **ttyACM2** | CH9114F UART0 | A55: U-Boot, Linux; getty `root`, no password |
| **ttyACM3** | UART1 | the M33 System Manager monitor |
| ttyACM4 | UART2 | M7 |

- The Yocto scripts default to `ttyACM0`/`ttyACM1`. Use `boardq.py --port /dev/ttyACM2` and `SM_PORT=/dev/ttyACM3 smmon.py`.
- `uartcap.py PORT LOG` logs a port read-only with DTR/RTS low.
- **One reader per tty. Stop a logger by its PID from `fuser`**, never with `pkill -f`, which matches the shell running it.

**The SM monitor is a second machine:**
- It has the M33's rights on the pad mux, clock roots, power domains, SRC and BLK_CTRLs: `md`/`mm`, `clock.r`, `power.r`, `cpu.r`, `reason`, `err`, `syslog`, `pmic.r <PF09/PF53 address> [reg]`, and `reset`.
- It runs the same commit (`44f76dcb`) as the source in `yocto-imx95/downloads/git2/github.com.nxp-imx.imx-sm.git`, so its board code can be read.
- It cannot read GPIO2 (prints `????????`, A55-owned).

## 6. Lessons

1. **A cache-coherent read beats a fast one when a system has just died.** The fast view showed a log that silently stopped.
2. **A hardware PC sampler tells "crashed" from "busy" from "idle" in one look:** `local_cpu_stop` is a panic, `cpu_do_idle` is WFI.
3. **Keep two independent consoles.** The SM on its own UART saw and reset a board whose A55 and debug port were both gone.
