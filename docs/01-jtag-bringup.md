# 01 — JTAG bring-up on the FRDM-IMX95-PRO with the CH347F (2026-10-16)

**Tags:** `[V]` measured · `[?]` not verified · `[R]` design files / reference manual.

## Summary

- **The JTAG port works.**
  - The TAP and both IDCODEs read.
  - The link is clean up to 15 MHz.
  - The debug port is already open in serial-download mode, without the debug mailbox or a signed authentication.
- **Proven so far:**
  - A55 core 0 was halted and resumed inside U-Boot.
  - The AHB-AP reads DRAM while the cores run.
- **Remote reset:** the chip can be reset over JTAG (MDM-AP `JTAG_SW_RST`) or from the System Manager's console, so a hung board no longer needs a hand on the power switch.

## 1. Rework

1. **Fly wires:** owner's photo [`../rework-20261016.jpg`](../rework-20261016.jpg), on TP1 TMS, TP2 TDO, TP4 TCK, TP5 TDI and TP7 GND, to the CH347F (VIO 3.3 V).
2. **First scan with the wires only:** `all ones`, and SWD `cannot read IDR`. `[V]` The test points reach the SoC only through R104/R125/R132/R135, which are not fitted (DNP in BOM-95794_B1). `[R]`
3. **Fix:** 0 Ω fitted on R104, R125, R132, R135 (owner). Then:

| Check | Result | |
|---|---|---|
| `ocd.sh scan` at 469 kHz | JTAGC `0x9893801d` (mfg 0x00e, bits 16:12 = 0x18), JTAG-DP `0x0ba03477` | `[V]` |
| `jtag-linktest.py --chip imx95` 469–15000 kHz | 0 bit errors at every clock (9,600 BYPASS bits + 300 IDCODEs each) | `[V]` |
| fixed-register re-reads (APB-AP ROM table, 28 words × 50) | 0 mismatches at 3.75, 7.5 and 15 MHz | `[V]` |

The speed is set by USB round trips, about 870 ms per 1,400 reads at any TCK. `[V]`

**Cross-check with the T113:** the same CH347F showed TDI errors on the LCPI-PC that morning, even at 469 kHz, and is clean here. The fault is on the T113 side.

## 2. The debug port, in serial download (SDP) mode

| | | |
|---|---|---|
| DP CTRL/STAT | `0xf0000001`: CDBGPWRUPACK and CSYSPWRUPACK both set | `[V]` |
| AP 0 IDR | `0x84770001`: AHB-AP (system bus) | `[V]` |
| AP 1 IDR | `0x54770002`: APB-AP (A55 debug, CoreSight) | `[V]` |
| AP 6 (MDM-AP) | IDR `0x001c0030`; MDMSTATUS / MDMSTATUS2 readable | `[V]` |

- This is the **normal** SWJ-DP; the AUTH DP would show the ISP-AP `0x002a0000` at AP 0.
- That answers the plan's open question for this OEM-open part (LC 0x40010, SRK hash zero): debug is open without the mailbox.
- AHB-AP CFG = 0, so its **addresses are 32-bit**. The debugger reaches only the low 4 GB of the 16 GB. `[V]`

## 3. The A55 debug blocks

- **APB-AP ROM table at `0x80000000`:**
  - entry 0 → `0x80800000`, the A55 cluster table;
  - entries for SWO, ETF, ETR and two CTIs at `0x8102`–`0x8106xxxx`.
- **The cluster table lists**, for each core: debug +0x10000, CTI +0x20000, PMU +0x30000, ETM +0x40000, and core n at +n·0x100000.
- **Core 0's debug block:** PIDR part `0xD05` (Cortex-A55) at **`0x80810000`**. `[V]`
- The RM's Table 76 gives `0x40810000`, so the APB-AP sees the RM's `0x4xxxxxxx` debug map at `0x8xxxxxxx`.
- **`imx95-pro.cfg`'s default is now `0x80810000`.**

**The trap:**
- In SDP mode the A55 cluster is powered off.
- `dap info 1` walks into `0x80800000`. The read never completes, and **every later APB-AP access fails until a power cycle**. A DP ABORT does not clear it. `[V]`
- `ocd.sh a55` no longer runs the full walk.

## 4. U-Boot and the halt

- **SDP:** `uuu ramboot.lst` boots U-Boot (2026.04) from RAM with its fastboot gadget. Nothing is written to the eMMC.
- **Halt:** core 0 halted in AArch64 at **EL2H** with the MMU and caches on, PC `0xfff38a30`. That instruction is `b` inside a register-read helper of U-Boot's USB polling loop, as the disassembly of the AHB-AP dump shows. `[V]`
- **Resume:** `resume`; MDMSTATUS2 then shows core 0 out of debug, and U-Boot still answers `fastboot getvar version-bootloader`. `[V]`
- **AHB-AP DRAM read:** a 256 KiB dump of U-Boot read twice was identical, at 97 KiB/s. `[V]`

## 5. Resetting the chip without touching the board

| Way | Needs | Result |
|---|---|---|
| MDM-AP MDMCONTROL bit 2 (`JTAG_SW_RST`): write 1, 100 ms, write 0 | JTAG; the SRC has it unmasked (SRMASK 0x1fcc, SRTMR 0 = level, SRMR_5 0 = full SoC reset) | full SoC reset; the ROM comes back in SDP within 1 s; the SM records `reason=jtag_sw` `[V]` |
| SM monitor `reset` (`smmon.py reset`, ttyACM3 here) | the debug USB | `reason=sw`; ROM SDP within 1 s `[V]` |

- The RM's text for `JTAG_SW_RST` says "0 = triggered", yet the bit reads 0 while nothing resets. **Writing 1 is what triggers it.** `[V]`
- The SoC-reset path is unusable when the APs themselves fail (see [02](02-linux-without-a-console.md) §4): the MDM-AP is an AP too. The SM monitor is then the way.

## 6. Lessons

1. **Read the test-point schematic before soldering:** a test point behind a DNP link is not connected to anything.
2. **Prove the link with data, not with a scan:** the link test, then fixed registers read many times.
3. **Don't let OpenOCD walk a ROM table into a powered-off domain.**
4. **Find the reset path before the board is out of reach:** a debugger-only reset turns a hang into a two-minute cycle.
