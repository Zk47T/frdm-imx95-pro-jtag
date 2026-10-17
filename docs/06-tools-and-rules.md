# 06 — The kit's tools, and the rules that cost something to learn

## Files

| File | What it does |
|---|---|
| `openocd/` | stand-alone OpenOCD files for others: `target/imx95.cfg`, `interface/ch347.cfg`, `board/nxp_frdm-imx95-pro.cfg` ([README](../openocd/README.md)) |
| `ocd.sh STAGE` | one OpenOCD run per stage: `scan`, `dp`, `dm`, `dm-reset`, `dac`, `a55`, `server`, `mdm`, `pcs`, `etm-ids`, `trace CORE BASE SIZE MS`, `trace-all BASE SIZE MS [CONFIGR]`, `read BASE SIZE FILE` (SWD, 276 KiB/s), `ktext` (the live kernel text), `top [SECS]` (PC + PID sampling of all A55s); `JTAG_KHZ=`, `SWD=1`, `DBG0=`; logs in `logs/` |
| `imx95-pro.cfg` | the DAP, and with `IMX95_A55` the AHB-AP/APB-AP memory targets, six A55s (SMP, hwthread), the M7 and the M33; A55 debug base `0x80810000` |
| `imx95-jtag.tcl` | IDCODEs, DP state, the debug mailbox (allow-listed commands only), MDM-AP, PC sampling, `etm_ids`, `cs_trace_start` / `cs_trace_stop`, `cs_multi_start` / `cs_multi_stop` (all cores; the stop is at the sink) |
| `console.sh start\|stop` | one long-running OpenOCD: RTT shell on `:9090`, Tcl RPC on `:6666` |
| `jsh.py 'CMD'` | one command through the JTAG shell |
| `ocdrpc.py 'TCL'` | the Tcl port; importable (`Ocd().read32()`, `.dump()`) |
| `dmesg.py [-c CORE] [-n N] [-g RE]` | the kernel log from outside; `-c` through a halted core |
| `linux.sh` | boot the DSI kernel + `board/out` DTB + initramfs through U-Boot's fastboot gadget; `VERBOSE=1`, `EXTRA_BOOTARGS=` |
| `uartcap.py PORT LOG` | log a board UART, read only, DTR/RTS low |
| `board/build.sh` | static arm64 busybox (the T113 rescue config), `rtt-console`, `physwin` → `board/out/` |
| `board/mkinitramfs.sh`, `board/init`, `board/jtagsh` | the JTAG initramfs |
| `board/mkdtb.sh` | the session DTB; `TRACE=1`, `I2CGPIO=1` |
| `board/physwin.c` | copy, read, write or checksum physical memory through `/dev/mem` (no-map regions only) |
| `board/scenario-real.sh [done]` | real work for a trace on the board's own image (sha256sum over `/usr`, udevadm, journalctl, systemctl), a PID map and `/proc/interrupts` before and after |
| `board/usbexport.sh PHYS LEN \| stop` | on the board (`/mnt`): a trace buffer as a read-only USB disk on the board's USB-C (41 MiB/s) |
| `trace/capture.sh NAME SECS [CMD]` | a whole-system trace in one command: trace-all while CMD runs on the board, PID map, USB readout with SHA-256 check, snapshot, summary |
| `trace/jtagtop.py SAMPLES` | the report for `ocd.sh top`: per core the busy share and tasks, the busiest tasks and functions; `--tty` or `--pids` for names, `--elf` for user code |
| `trace/usbget.sh OUT` | on the PC: read that disk into OUT, with its speed and SHA-256 |
| `board/i2cblk.c` | a tiny `/dev/i2c-*` SMBus helper (scan, block read); cross-compiled and pushed, for [09](09-detecting-a-kernel-fix.md) |
| `board/i2cbang.sh SDA SCL ADDR` | one I2C address probe clocked by hand through GPIO2, printing every pin state |
| `trace/mksnap.py` | OpenCSD snapshot + decode + per-function instruction counts; `--cores`, `--kernel-live`, `--pids` |
| `trace/tracetool.py` | index (per core, per task), Perfetto (protobuf or JSON), speedscope, folded stacks, `nav`; `--window`; `summary` and `cut` for captures too big to index |
| `frmd-imx95-pro/` | the board's documents: UM12527, the QSG, NXP's design files (schematic netlist, BOM, layout, Gerbers), the annotated pictures |

External pieces, all in the elb1 repo:
- OpenOCD with the CH347 driver: `11.tmp/openocd-build`, and `11.tmp/ocd-fast-build` for RTT on a mem_ap.
- `6.host-tools/jtag-ch347/{ch347.cfg,jtag-linktest.py,kmem.py}`.
- OpenCSD: `11.tmp/opencsd/trc_pkt_lister.sh`.
- The busybox source: `11.tmp/br-dl/busybox/`.

## A session, start to finish

```
# 1. board: SW4 = x,OFF,OFF,ON (serial download), debug USB, CH347F; power on
lsusb | grep 1fc9:015d
# 2. U-Boot from RAM (writes nothing)
cd yocto-imx95-pro/sources/meta-fastboot-imx95/scripts
source board-env.sh; cp -L "$BOOTSTRAP" _flash.bin; SUDO_PASS=... sudo -A uuu ramboot.lst
# 3. Linux from RAM with the JTAG shell
cd jtag; board/mkdtb.sh; board/mkinitramfs.sh; SUDO_PASS=... ./linux.sh
# 4. look
python3 uartcap.py /dev/ttyACM2 logs/a55.txt &          # or: boardq.py --port /dev/ttyACM2 'CMD'
./ocd.sh pcs 0 200; ./dmesg.py -n 40
./ocd.sh trace 0 0xf1000000 0x1000000 300
# 5. back to SDP without touching the board
SM_PORT=/dev/ttyACM3 smmon.py reset
```

## Rules

1. **Nothing writes fuses or the life cycle.** `imx95-jtag.tcl`'s mailbox code allows only START, GET_CRP_LEVEL, EXIT, START_DBG_SESSION and DBG_AUTH_START.
2. **Never read the A55 cluster (`0x808xxxxx` on the APB-AP) while it is off** (SDP, before U-Boot): the APB-AP wedges until a power cycle. No `dap info 1` there.
3. **The AHB-AP sees only the low 4 GB.** Put anything the debugger must read below `0x1_0000_0000` (no-map), and copy the rest down with `physwin`.
4. **Pad tests on this SoC: read a pad through its GPIO only when it is muxed as GPIO.** PDIR reads 0 for a pad in another mode even with SION ([03](03-dsi-and-the-i2c-fault.md) §4).
5. **Linux can't touch the CoreSight system blocks** (`0x41030000`): an ETF/ETR node in the DT panics the boot. Trace from the debugger.
6. **SW4 on serial download:**
   - Don't `reboot` a RAM-booted Linux; SPL ends in a USB-SDP fault loop.
   - Reset with the SM (`smmon.py reset`) or JTAG (`JTAG_SW_RST`) instead.
   - Set SW4 back to eMMC (**x,OFF,ON,OFF**, UM12527 Table 7) for normal boots.
7. **ttyACM numbering depends on plug order.** Check `/sys/class/tty/ttyACM*/device` before opening a port. One reader per port. Kill by PID, never `pkill -f`.
8. **The SM monitor's `mm` writes act at once and with full rights.** Read the register first, write, then restore and read back; log every write (this was done for every pad experiment in doc 03).
9. **The CH347F on a hub port re-enumerates.** Prefer a PC port. A long-running OpenOCD can drift into stale reads: restart it before trusting odd values.
10. **The full trace decode is about 300 MB of text per MB of trace.** `mksnap.py` keeps 2,000 lines and the summary.
11. **Stop a multi-core trace at the sink** (`cs_multi_stop`): stopping the ETMs one by one takes ~1.8 ms each over the CH347F and gives every core a different end ([04](04-arm-trace-into-dram.md) §7.1).
12. **Decode against the live kernel text** (`ocd.sh ktext`, `mksnap.py --kernel-live`), read again after a kernel rebuild. Linux patches 0.4 % of its text at boot; against `vmlinux` the call stacks drift ([04](04-arm-trace-into-dram.md) §8.1).
13. **Read big captures through the board's USB** (`usbexport.sh`, `usbget.sh`), not the debug port: 41 MiB/s against 266 KiB/s. Process them with `tracetool.py summary`, and `cut` windows for detail.

## Where things are (outside this directory)

| What | Where |
|---|---|
| This kit | `/home/zk47/Project/5.elb1-t113/16.frdm-imx95-pro-jtag/` (own git); `/home/zk47/Learning/yocto-imx95-pro/jtag` is a symlink to it |
| Design files (schematic, netlist, layout, Gerbers, BOM) | `frmd-imx95-pro/FRDM-IMX95-PRO-DESIGNFILES/` here (identical to elb1 `4.docs/1.manual/frmd-imx95-pro/`) |
| **Annotated pictures** (rework spots, test points, power, buttons, debug UARTs) | [`frmd-imx95-pro/pictures/`](../frmd-imx95-pro/pictures/README.md) |
| The Pro's Yocto tree and its helper scripts (`ub2.py`, `b.sh`, `post_recover.sh`, `recover.sh`, `smmon.py`) | `/home/zk47/Learning/yocto-imx95-pro/sources/meta-fastboot-imx95/scripts/`; board notes in `/home/zk47/Learning/yocto-imx95-pro/handoff/PRO.md` |
| i.MX95 RM, system debug chapter (text) | `/home/zk47/Learning/yocto-imx95/0.documentation/rm/p00-general/ch014-system-debug.md` |
| The June attempt on the non-Pro FRDM-IMX95 (J-Link clone; notes, configs) | `/home/zk47/Learning/yocto-imx95/jtag/` (`jtag-note`) |
| NXP SPSDK (the mailbox protocol's source, `spsdk/dat/`) | `~/.local/pipx/venvs/spsdk` |
| NXP forum threads (the owner's "JTAG on FRDM iMX95", the solved thread 2386639, J-Link on FRDM-IMX93) | elb1 `4.docs/1.manual/jlink-imx93/` |
| OpenOCD with the CH347 driver | `11.tmp/openocd-build/src/openocd`, scripts `1.repo/3.other/openocd/tcl` (`ocd.sh` finds both) |
| CH347 setup, udev rules, quirks | elb1 `6.host-tools/jtag-ch347/README.md` |
