# JTAG, trace and board debugging on the FRDM-IMX95-PRO (CH347F)

NXP FRDM-IMX95-PRO: i.MX 9596 (six Cortex-A55, M7, M33), 16 GB LPDDR5. The kit debugs it through the
JTAG test points with a CH347F and OpenOCD, the same probe as the T113 work. It also covers Linux
without a console, ARM instruction trace into DRAM, and the board's DSI/I2C fault.
A local git repository (2026-10-16); its history starts with the import from elb1 `ed4a0fa`.

## Status, 2026-10-16 evening

| | | |
|---|---|---|
| JTAG | works: 0 Ω links fitted on R104/R125/R132/R135; clean to 15 MHz; **debug open in serial download**, no mailbox needed | [01](docs/01-jtag-bringup.md) `[V]` |
| A55 | halted and resumed in U-Boot; debug base `0x80810000`; PC sampling (PMPCSR) while running | [01](docs/01-jtag-bringup.md), [02](docs/02-linux-without-a-console.md) `[V]` |
| Reset from afar | MDM-AP `JTAG_SW_RST` (full SoC reset) or the SM monitor `reset` | [01](docs/01-jtag-bringup.md) §5 `[V]` |
| Linux without flashing | U-Boot over SDP, then the DSI kernel + a JTAG DTB + an initramfs from RAM; eMMC untouched | [02](docs/02-linux-without-a-console.md) `[V]` |
| Shell over JTAG | RTT console works; **one board hang while it was in use, open** | [02](docs/02-linux-without-a-console.md) §4 `[V]` `[?]` |
| **ARM trace** | **ETM → ETF → ETR into a 2 GB DRAM buffer, 1.58 GiB in 8 s; decoded with OpenCSD, 0 errors; instruction count matches the code to 1 in 3,074**; Linux's own CoreSight driver panics this BSP | [04](docs/04-arm-trace-into-dram.md) `[V]` |
| **DSI** | dark because the panel's 3.3 V needs U17 (PCAL6524, I2C4), and **no device on I2C3 or I2C4 answers**; the SoC side of both buses is proven healthy; the 2026-09-15 "lines held low" was a measurement artefact | [03](docs/03-dsi-and-the-i2c-fault.md) `[V]` |
| Probe | CH347F clean to **60 MHz** JTAG; SWD works; best bulk read **276 KiB/s (SWD 5 MHz, memaccess 2)**; JTAG capped ~105 KiB/s by the chip's bit mode | [07](docs/07-ch347f-m7-and-other-probes.md) `[V]` |
| M7 | examined, halted, resumed and debugged with gdb through OpenOCD's port 3334, over the same JTAG; usable from MCUXpresso/VS Code as a GDB server | [07](docs/07-ch347f-m7-and-other-probes.md) §2 `[V]` |
| Trace views | **all six A55 cores at once** (one Perfetto track each; overflow when 5 are busy); call stack rebuilt from the trace; Perfetto timeline, speedscope, cycle-weighted flame graph, and a navigator that steps forward and back with source; **timestamps (6.144 G/s) and cycle counts (1.807 GHz) verified** | [08](docs/08-trace-visualisation.md) `[V]` |
| Next for the owner | meter checks in [03](docs/03-dsi-and-the-i2c-fault.md) §6; option: fit R75 + R194 to power the panel without U17; CH347F I2C on J6 to probe U17 directly ([07](docs/07-ch347f-m7-and-other-probes.md) §1) | |

**Where the hardware was left:**
- the CH347F wired to this board (the T113 labs have no JTAG meanwhile);
- the debug USB in; SW4 on serial download;
- Linux running from RAM with the i2c-gpio DTB.

**For normal boots:** SW4 x,OFF,ON,OFF.

## Documents

| | |
|---|---|
| [01 JTAG bring-up](docs/01-jtag-bringup.md) | rework, link, debug port, A55 base, halt, the APB-AP trap, resets |
| [02 Linux without a console](docs/02-linux-without-a-console.md) | boot from RAM, shell over JTAG, dmesg and PC sampling from outside, UART map, the SM monitor |
| [03 DSI and the I2C fault](docs/03-dsi-and-the-i2c-fault.md) | the probe chain, 17 checks, the correction, meter checks, workarounds |
| [04 ARM trace into DRAM](docs/04-arm-trace-into-dram.md) | ETM/ETF/ETR from the debugger, 2 GB buffer, decode, four cross-checks |
| [05 i.MX95 vs T113-S3](docs/05-imx95-vs-t113.md) | what this debug path gives that the T113 can't; SDP vs FEL |
| [06 Tools and rules](docs/06-tools-and-rules.md) | every script; a session start to finish; the rules |
| [07 The CH347F, the M7, other probes](docs/07-ch347f-m7-and-other-probes.md) | link and speed benchmark; GPIO/UART/I2C/SPI of the chip; M7 over the same port, MCUXpresso; J-Link and TRACE32 compared |
| [08 Trace visualisation](docs/08-trace-visualisation.md) | call-stack rebuild, Perfetto/speedscope/flame graph, time axis from timestamps and cycles, back-and-forth navigation, what TRACE32 adds |
| [`frmd-imx95-pro/`](frmd-imx95-pro/pictures/README.md) | UM12527, QSG, NXP design files, annotated pictures (the netlist, BOM and pictures used above) |

Tags in all documents: `[V]` measured, `[?]` not verified, `[R]` from the design files or reference manual.

## Corrections to the plan below (written 2026-10-15, before the board was run)

| The plan said | Measured |
|---|---|
| A55 debug base 0x40810000 (RM Table 76) | **0x80810000** as the APB-AP sees it; `imx95-pro.cfg` default changed |
| `ocd.sh a55` runs `dap info 1` | removed: in SDP it wedges the APB-AP |
| the debug mailbox (§6) may be needed | not on this part: the normal DP answers in SDP |
| `ttyACM0` = A55, `ttyACM1` = SM | only if the board's debug USB is plugged in first; here ttyACM2/3 |
| trace through the Linux CoreSight driver (§8) | the driver's first ETF read aborts and kills init; trace from the debugger instead |
| docs and commits go to the elb1 repo | this directory is now its own git repository (owner, 2026-10-16) |

---

# Appendix: the plan of 2026-10-15, as written

## 1. What we know about the board's debug port

From the netlist (`allegro/pstxnet.dat`), the layout and RM ch. 14. Each point is checked in the pictures.

- **The pads:** U25 balls AG21 TCK, AH22 TMS, AK24 TDI, AJ23 TDO, in bank NVCC_CCM_DAP.
- **The supply: 3.3 V.**
  - NVCC_CCM_DAP's balls AC23/AD24 are on VDD_3V3, and so are VTREF (TP6, through R20 100 Ω), the pull-ups and the level shifter's SoC side.
  - The schematic's power-tree table says 1.8 V. That table is wrong (picture 02).
- **The pads go two ways** (picture 03):

  | Pad | JTAG | LPUART5 / M.2 | to its test point (DNP 0 Ω) | to the BT UART (fitted 49.9 Ω) | TP |
  |---|---|---|---|---|---|
  | AH22 | TMS | RTS | R104 | R411 | TP1 |
  | AJ23 | TDO | TX | R132 | R428 | TP2 |
  | AG21 | TCK | CTS | R125 | R420 | TP4 |
  | AK24 | TDI | RX | R135 | R434 | TP5 |

  - TP3 = RST (reaches SYS_RST_B only through D3, not fitted).
  - TP6 = VTREF (3.3 V), TP7 = GND.
- **The Bluetooth path** (picture 05) goes through U57 (LSF0204, always enabled; 3.3 V ↔ 1.8 V) to the M.2 Key-E slot J8.
  - **The Wi-Fi/BT card ships fitted** (assembly note 10). Its TX drives TDI and its RTS drives TCK.
  - U57's side adds a 4.7 kΩ pull-up to 3.3 V on every line: harmless.
- **The two resistor sets share one spot** (pictures 09, 12, 13): R411/R420/R428/R434 on the bottom sit exactly under R104/R125/R132/R135 on the top. Same 0402 footprint, same X/Y.
- **Test points** (pictures 08, 12): 0.89 mm round pads on the top, 1.52 mm apart.

  | | | | |
  |---|---|---|---|
  | TP1 TMS | TP2 TDO | | TP3 RST |
  | TP4 TCK | TP5 TDI | TP6 VTREF | TP7 GND |

  On the test-point side, TMS and TDI are pulled up 10 kΩ (R17, R18); TDO's pull-up R19 is not fitted.
- **R128** is a 10 kΩ pull-down from TCK to GND, not fitted. **It is not a link: never fit 0 Ω there.**
- **Reset:**
  - K1 (the side button) pulls SYS_RST_B low; its pad 3 is SYS_RST_B.
  - Holding K2 and pressing K1 boots from the TF card (the recovery path).
- **Debug USB J22** (picture 07): a CH9114F with four UARTs.
  - UART0 = the A55 console (`/dev/ttyACM0`), UART1 = the M33 System Manager (`ttyACM1`), UART2 = the M7.
  - Its DTR/RTS pins are all unconnected. There is no on-board SWD/JTAG probe.
- **Linux takes the pads:** the board DTS muxes them to LPUART5 for Bluetooth (`pinctrl_uart5`). **U-Boot's DT leaves LPUART5 disabled**, so at the U-Boot prompt the pads stay JTAG (mux mode 0).
- **The chip's debug logic** (RM 14.3):
  - **One port, two SWJ-DPs behind a mux.**
    - Before debug is authenticated, the port reaches **SWJ-DP (AUTH)**: AP 0 = ISP-AP, the debug mailbox (IDR `0x002A0000`); AP 1 = JTAG-AP.
    - After it, the **normal SWJ-DP** (Table 77): AP 0 AHB-AP (system bus), AP 1 APB-AP (A55 debug, CoreSight), AP 2 the M7, AP 3 the M33, AP 6 MDM-AP, AP 10 GPU, AP 11 NPU.
  - **The IR is 8 bits**, and the JTAGC (boundary scan) and the JTAG-DP share it. The JTAGC decodes the IR and muxes TDO, so **the chain shows one TAP**.
    - After a TAP reset its IDCODE is the JTAGC's: bits 16:12 = 11000, mfg 0x00e (the i.MX93's is 0x0892801d).
    - The DP answers DPACC 0xFA, APACC 0xFB, ABORT 0xF8, IDCODE 0xFE. Our OpenOCD's `adi_v5_jtag.c` already sends those.
    - The June config for the non-Pro (one TAP, IR 8) was right; its "all zeroes" scan was contact or pin mux.
  - **A55 debug blocks** (Table 76): core n's debug block is at 0x40810000 + n × 0x100000. Its CTI is at +0x10000, its PMU at +0x20000, its ETM at +0x30000.
  - **CoreSight:** SWO 0x41020000, ETF 0x41030000 (16 KB), ETR 0x41040000. **There is no TPIU**: trace goes to the ETF or, through the ETR, into DRAM. SWO comes out on TDO.
  - **The mailbox protocol** (SPSDK `dat/debug_mailbox.py`, `dm_commands.py`):
    - registers: CSW 0x00 (RESYNCH_REQ 0x01, REQ_PENDING 0x02, CHIP_RESET_REQ 0x20), REQUEST 0x04, RETURN 0x08, IDR 0xFC;
    - a command is `REQUEST = cmd | nparams << 16`, with 0xA5A5 handshakes;
    - status `0x00100001` = START first, `0x00100002` = not allowed in this life cycle.

## 2. Rework and wiring

The owner's choice. Either way: **R128, D3 and R19 stay empty.**

**A. Move the four 49.9 Ω resistors (recommended: no new parts, the M.2 card can stay in):**

| Take off (bottom) | Put on (top) | Signal |
|---|---|---|
| R411 | R104 | TMS |
| R420 | R125 | TCK |
| R428 | R132 | TDO |
| R434 | R135 | TDI |

- JTAG then has 49.9 Ω in series, which is fine.
- Bluetooth is cut off until they go back.

**B. Fit 0 Ω (0402) on R104, R125, R132, R135 and pull the Wi-Fi/BT card from J8.** Unplug its two antenna cables first.

**Wiring to the CH347F** (same name to same name, as on the T113):

| CH347F | Board |
|---|---|
| TMS | TP1 |
| TDO | TP2 |
| TCK | TP4 |
| TDI | TP5 |
| GND | TP7 |

- **TP6 (3.3 V):** for the meter only. The module's VIO jumper stays at 3.3 V; don't join the two supplies.
- **TP3:** leave it open.
- **Wire:** thin (30 AWG wire-wrap or enamelled), under 10 cm, taped or glued down so the pads don't lift. NXP's solved thread 2386639 was fixed by nothing more than short wires.

**Checks before the first scan** (§0 step 2):
- TP4 to TP7 is not 0 Ω: TCK is not shorted.
- TP6 to TP7 is not 0 Ω.
- Powered, TP6 reads 3.3 V.

## 3. Put the board where the pads are JTAG

The board runs the fast-boot image: SPL boots Linux directly (falcon mode). Holding a key at power-on does **not** stop it (tested by the earlier work, see `ub2.py`'s header). The board is at `192.168.1.113` (`b.sh`, `board-env.sh`).

**At the U-Boot prompt** (steps 4–7):
```
$ cd /home/zk47/Learning/yocto-imx95-pro/sources/meta-fastboot-imx95/scripts
$ ./ub2.py version
```
- `ub2.py` renames `kernel-atf-dtb-container.img` on the boot FAT to `.off` over ssh, reboots, and keeps the serial line busy until `u-boot=>`.
- SPL then falls through to U-Boot, which waits. Linux must be up on the network for this.

**Linux, with the pads left to JTAG** (step 7 on Linux, step 8):
```
$ scp /home/zk47/Project/5.elb1-t113/16.frdm-imx95-pro-jtag/imx95-19x19-frdm-pro-jtag*.dtb root@192.168.1.113:/tmp/
$ ./b.sh 'mkdir -p /mnt/boot; mount /dev/mmcblk0p1 /mnt/boot; cp /tmp/imx95-19x19-frdm-pro-jtag*.dtb /mnt/boot/; sync; umount /mnt/boot'
$ ./ub2.py 'setenv fdtfile imx95-19x19-frdm-pro-jtag.dtb' --boot
```
- The JTAG DTB is the deployed `imx95-19x19-frdm-pro.dtb` with one change: `/soc/bus@42000000/serial@42590000` (LPUART5) disabled.
- The trace DTB also enables `/soc/etm@40840000`, `/soc/funnel`, `/soc/funnel-sys`, `/soc/etf@41030000` and `/soc/etr@41040000`.
- To keep the JTAG DTB across reboots, add `saveenv` at the prompt. To undo it: `setenv fdtfile imx95-19x19-frdm-pro.dtb; saveenv`.

**Back to the normal fast boot:** `./post_recover.sh` (renames the container back), then refit the BT parts or the card.

**Serial:** `ttyACM0` = A55 (U-Boot, Linux), `ttyACM1` = System Manager.
- `PRO.md` warns that a DTR pulse resets the board into serial download. The Pro's CH9114F has no DTR/RTS connected, so that can't happen here. The scripts keep DTR low anyway.
- Two readers on one tty split the stream: stop any capture before using `ub2.py`.

## 4. Does JTAG see the chip? (wiring)

First the link test of §10 (`jtag-linktest.py --chip imx95`). It needs only the TAP, so it answers "are the four wires good" before anything else. Then:

```
$ JTAG_KHZ=469 ./ocd.sh scan          # or the highest clock the link test passed
```
*You should see:*
```
Info : JTAG tap: imx95.cpu tap/device found: 0x…01d (mfg: 0x00e (Freescale (Motorola)), …)
JTAGC IDCODE 0x…  (bits 16:12 = 0x18, the RM says 0x18)
JTAG-DP IDCODE 0x…477
```
*What it means:*
- One TAP with an 8-bit IR; after a reset it shows the JTAGC's IDCODE.
- IR 0xFE switches TDO to the ARM JTAG-DP, whose IDCODE ends in `477`. Both answering proves all four signals.
- Record both values. `-c 'set IMX95_TAPID 0x…'` then makes later runs check the first.

*If instead:*

| You see | It means |
|---|---|
| `all zeroes` / `all ones`, `IR capture error` | TDO not connected, a link missing, or the pads are not JTAG (Linux running with the normal DTB). Check §3 first |
| a TAP, but the JTAG-DP IDCODE is 0 or `ffffffff` | the IR isn't reaching 0xFE: TDI or TMS |
| `Unexpected idcode after end of chain` | more than one TAP. Record it; the config assumes one |
| `LIBUSB_ERROR_IO`, `CH347 open error` | the probe, not the board: §10 |

## 5. Which debug port answers?

```
$ ./ocd.sh dp
```
*Expected, before authentication:*
```
DP CTRL/STAT 0x…: CDBGPWRUPACK 1, CSYSPWRUPACK 0, …
AP 0 IDR 0x002a0000 the debug mailbox: SWJ-DP (AUTH), debug not authenticated
```

| Result | Next |
|---|---|
| AP 0 = `0x002a0000` | the AUTH DP: §6 |
| AP 0 a MEM-AP, CSYSPWRUPACK 1 | debug is already open: §7 |
| CDBGPWRUPACK 0, nothing readable | the DP refuses even debug power: record the log, stop |

The config creates the DAP with `-ignore-syspwrupack`, and `ocd.sh` runs `catch init`, so a refused power-up still leaves the mailbox reachable.

## 6. Open debug through the mailbox

**a. Without a chip reset** (the ELE may serve the mailbox while U-Boot runs):
```
$ ./ocd.sh dm
```

**b. With a chip reset**, only if (a) ends in `dm: no answer to the resync in 5 s`.
- **First make the JTAG DTB the default** (§3, `setenv fdtfile …` + `saveenv`). After the session the boot continues into Linux, and the normal DTB would take the pads.
- Then:
```
$ ./ocd.sh dm-reset
```

| `dm_session` prints | Meaning | Next |
|---|---|---|
| `START_DBG_SESSION -> 0x00000000, success`, then AP 0 a MEM-AP | **debug is open** | §7 |
| `success`, AP 0 still `0x002a0000` | accepted, not opened | try (b), then §7 anyway |
| `0x00100002` (not recognised in this life cycle) | the part wants a signed authentication | `./ocd.sh dac` saves the challenge to `dac-<time>.txt`. **Stop, report, ask the owner**: the answer needs SPSDK and the part's keys |
| `0x00100001` | the script sends START itself and retries | — |
| WAITs and timeouts on every read | nobody serves the mailbox | (b); if (b) also fails, record the logs |

The non-Pro failure in June (`Could not power-up system power domain` after a clean scan) is the case this step is meant to fix.

## 7. Halt an A55

```
$ ./ocd.sh a55            # if §6 opened debug in an earlier run
$ ./ocd.sh a55 dm         # mailbox and halt in one run
```
*You should see:* `dap info 0` an AHB-AP, `dap info 1` an APB-AP with a ROM table, then core 0 halted with `pc` in U-Boot, then resumed.

*If `arp_examine` fails while `dap info 1` worked:* compare the ROM table's A55 addresses with the config's `0x40810000`.
- The APB-AP may see them elsewhere: i.MX8M's sits at `0x80410000`.
- `DBG0=0x80810000 ./ocd.sh a55` (or whatever the ROM table shows).

**gdb:** `./ocd.sh server dm`, then `gdb-multiarch -ex 'target extended-remote :3333'`.
- The six A55s are one SMP group, and gdb sees them as threads (`-rtos hwthread`).
- Cores 1–5 exist only after Linux starts them: `imx95.a55.N arp_examine`.
- For the kernel: the Yocto `vmlinux` (with DWARF) in `/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work/imx95_19x19_lpddr5_frdm_pro-poky-linux/linux-imx/6.18.20+git/build/`.
  - The kernel's `lx-` gdb scripts (as `lx-dmesg` on the T113, doc 79 §3) are not built there: `CONFIG_GDB_SCRIPTS` is off.
  - Build them with `make scripts_gdb` in that build dir, or rebuild with the option on.

**Memory without halting:** `imx95.ahb mdw ADDR`, `dump_image`.

## 8. Trace: into RAM, because there is no TPIU

- **From Linux, no probe:** boot the trace DTB (§3).
  ```
  # ls /sys/bus/coresight/devices/
  # perf record -e cs_etm/@tmc_etr0/u --per-thread taskset -c 0 PROGRAM
  ```
  - The Pro's kernel has CoreSight built in (`CONFIG_CORESIGHT_SOURCE_ETM4X=y`, `..._LINK_AND_SINK_TMC=y`).
  - Its DT describes only core 0's path.
  - `perf report` needs perf built with OpenCSD.
- **From JTAG:** once §6 has opened debug, read the ETR's buffer in DRAM through the AHB-AP, or the ETF through its registers.
- **Not verified:** whether the ETR works behind the SMMU, and the sink's real name (`ls` shows it).

## 9. What else the CH347F and OpenOCD can do here, in theory

Everything needs §6, except boundary scan. The procedures exist in `imx95-jtag.tcl`; none has run on the chip.

| | How | Needs | Confidence |
|---|---|---|---|
| Halt, step, breakpoints on the A55s (TF-A to Linux) | §7 | §6 | high |
| Memory while everything runs | `imx95.ahb` | §6; the TRDC may fence some areas off | high for DRAM |
| The M7 (NXP's demo runs on it from TCM) | `imx95.m7`, cortex_m on AP 2 | §6 | high |
| The M33 (System Manager, secure) | `imx95.m33` on AP 3 | its debug enables from the ELE | low |
| Who is halted, asleep, in WFI, without touching the cores | `./ocd.sh mdm [N]`: MDMSTATUS, and MDMSTATUS2's per-core WFI bits sampled | §6 | high (RM 14.4) |
| A profile of Linux without halting it | `./ocd.sh pcs CORE N`: `a55_pcs_where` finds PMPCSR (v8.2, PMU block 0x200/0x204) or EDPCSR (v8.0, 0xA0/0xAC); addresses to `addr2line` | the register | medium; a C port of our T113 OpenOCD change 4 to aarch64 would be the fast version |
| Instruction trace by hand | ETM (+0x30000), ETF, ETR with `mww`, `dump_image`, decode with OpenCSD | OpenOCD has no ETMv4 support | medium |
| Event counters of a running core | the PMU block (+0x20000) over the APB-AP | §6 | medium |
| SWO (ITM of the M7/M33) | `SWD=1` frees TDO, which carries SWO; CS-SWO at 0x41020000; capture with the CH347F's UART (NRZ) | the trace clock rate | low |
| A reset line | **SWD only** (the ch347 driver drives SRST only in SWD mode, on its TRST pin = GPIO3, open drain): fit D3, or wire K1 pad 3; `reset_config srst_only srst_open_drain` | rework | high; without it `dm_resync 1` resets through the mailbox |
| Pin states (boundary scan) | JTAGC SAMPLE (IR 0x02/0x03), EXTEST 0x04 | NXP's BSDL for the i.MX95 (not found online 2026-10-15); JTAG only | medium |
| The PHY TAPs (USB3, PCIe ×2, Ethernet) | TLM: IR 0x20 picks port 1–4 | NXP's test docs | low |

**SWD instead of JTAG:** `SWD=1 ./ocd.sh dp` and the other stages, except `scan`.
- Two wires: TMS = SWDIO, TCK = SWCLK. The CH347's SWD clock stops at 5 MHz.
- The mailbox works the same; SPSDK itself uses SWD on this chip.

**Speed:** on the T113 the CH347 moved memory at 16 KiB/s at any TCK. That was OpenOCD's 255 idle clocks per MEM-AP access, which the ch347 driver clocks in its slow bit mode (doc 80 §1).
- `imx95_fast` (called by `ocd.sh a55`/`server`) sets 8 on the AHB-AP once debug is open: expect ~100 KiB/s, as on the T113.
- Fine for control, registers, a kernel log, a 16 KB ETF dump.
- A 1 MB ETR buffer takes about 10 s.
- PC sampling takes two reads a sample: expect hundreds to a few thousand samples a second.

**Never:** the ELE (EdgeLock) is closed to every debugger.

## 10. The CH347F: what we learned on the T113

From `6.host-tools/jtag-ch347/README.md`, docs 47–79 and **doc 80 §21–§23 (2026-10-16)**.

**Its clocks are 468.75 kHz × 2ᵏ:** 469, 938, 1875, 3750, 7500, 15000, 30000 kHz.
- OpenOCD rounds any other value **up**: "1000 kHz" gives 1.875 MHz.
- `ocd.sh` takes `JTAG_KHZ=…`; without it, `ch347.cfg`'s 469 kHz.

**Verify the data, not just that commands succeed.** On the T113, after the module's wiring changed:
- 15 MHz stopped working;
- **7.5 MHz returned silently wrong memory**: 2,686 of 20,480 words across five re-reads, every command "successful";
- a scan alone proved nothing. The BYPASS test was clean at 7.5 MHz while memory reads failed.

So:
1. **`jtag-linktest.py --chip imx95 [KHZ…]`** before trusting a clock.
   - TAP only: random patterns through BYPASS (IR `0xff`) and the JTAG-DP's IDCODE (IR `0xfe`, the reference is the value read most often).
   - Bit errors per clock, their direction and their bit positions. A clean link shows 0 at every clock you use.
   - **How to read it:** IDCODE clean but BYPASS errors means the PC→chip line (**TDI**) is bad, because IDCODE needs only TDO. Errors in both point at TCK, TMS or the ground.
2. **Once debug is open (§6):** re-read something constant and compare before believing a dump. The CoreSight component ID registers (`+0xFF0`–`0xFFC` of every component: `0x0D`, `0x?0`, `0x05`, `0xB1`) never change.
3. **Don't halt, step or write while the link check fails.** On the T113 a board hung when debug transactions on a halted core went wrong over a bad link.

**A cross-check worth recording.**
- The T113's link was failing in exactly the TDI way on 2026-10-16: IDCODE clean, BYPASS errors even at 469 kHz.
- If `jtag-linktest.py` shows the same pattern here, on other wires and another board, the CH347F's TDI output or its Dupont lead is the suspect, not the T113's sniffer.
- If it's clean here, the T113 side is.
- Either way, note it in both READMEs.

| Symptom | Do |
|---|---|
| `CH347 not found`, `LIBUSB_ERROR_IO`, `CH347 open error`, `unable to get firmware version` | check `lsusb` for `1a86:55de`; `sudo usbreset 1a86:55de`, wait 4–6 s, retry |
| it disconnects and re-enumerates on its own (it did on a hub port, 2026-10-16 13:38) | move it to a PC port, not the hub; replug the module |
| fails again mid-transfer, or every transfer over 256 bytes fails | **unplug and replug the module** (a USB reset or a hub port cycle doesn't cut its power) |
| large reads fail | read in chunks (16 words worked) |
| scans work but memory looks odd | lower `JTAG_KHZ` one step and compare two reads |

- Module: CH347F, USB `1a86:55de`, chip 1.20. I/O follows its VIO jumper (3.3 V for both boards).
- Udev rules: `6.host-tools/jtag-ch347/host-setup.sh`.
- `ocd.sh` logs every run to `logs/<stage>-<time>.txt`. Keep the logs; they are the evidence for the status doc.
- **Wire length:** the T113 ran 15 MHz through a ~10 cm sniffer that morning. These fly wires plus Dupont leads are longer: start at 469 kHz and step up only on a clean link test.

## 11. Files

| File | What it is |
|---|---|
| `imx95-pro.cfg` | OpenOCD config, JTAG or SWD. The DAP (`-ignore-syspwrupack`); with `IMX95_A55`, the six A55s (SMP, hwthread), the M7, the M33 and memory targets on the AHB-AP and the APB-AP, all `-defer-examine`. `IMX95_DBG0` moves the A55 base |
| `imx95-jtag.tcl` | `imx95_ids`, `dm_status`; the mailbox (`dm_resync`, `dm_cmd` with the allow-list, `dm_session`, `dm_dac`, `dm_exit`); the MDM-AP (`mdm_status`, `mdm_idle`); PC sampling (`a55_pcs_where`, `a55_pcs`) |
| `ocd.sh` | one stage per call: `scan`, `dp`, `dm`, `dm-reset`, `dac`, `a55`, `server`, `mdm`, `pcs`; `SWD=1`, `DBG0=` |
| `imx95-19x19-frdm-pro-jtag.dtb` | Linux DTB, LPUART5 disabled |
| `imx95-19x19-frdm-pro-jtag-trace.dtb` | the same plus core 0's trace path enabled |
| `logs/`, `dac-*.txt`, `pcs-*.txt` | written by the runs |
| `../6.host-tools/jtag-ch347/jtag-linktest.py` | the link test of §10 (`--chip imx95`), in the elb1 repo |

## 12. After the session

1. **A course-style status doc** in `4.docs/2.status/`:
   - check `ls 4.docs/2.status` for the number (81 was next on 2026-10-16; another session also writes docs);
   - behaviour, checks, fix, lessons;
   - `[V]` measured, `[?]` not verified.
2. **This README:** the status line at the top, and §0's table (what's done).
3. **The top README:** one row in §3.
4. **Memory note:** `owner-test-boards`, the i.MX95 lines (what worked, the open question's answer).
5. Commit in area commits on `main` (Co-Authored-By trailer), `git fetch` first, push (§0, "Starting from the Yocto repo").
6. **Where the CH347F is now:** on this board or back on the LCPI-PC's sniffer. Write it in the status doc and in this README's status line: the T113 labs have no JTAG while it's here.
7. **The link-test result** (§10's cross-check): add it to `6.host-tools/jtag-ch347/README.md` as well.
