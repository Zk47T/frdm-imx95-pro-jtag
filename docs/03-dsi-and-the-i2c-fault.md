# 03 — The DSI panel stays dark: down to the I2C3/I2C4 devices

**Board:** FRDM-IMX95-PRO, SW4 on serial download, RAM-booted (U-Boot 2026.04 over SDP, then the
DSI kernel 6.18.20 + `board/mkdtb.sh` DTB + JTAG initramfs; rootfs on eMMC, untouched).
**Date:** 2026-10-16. **Tags:** `[V]` measured today · `[?]` not verified · `[R]` read from the design files.

## Summary

- The display pipeline never registers because a power chain never comes up. `[V]`
- The chain starts at U17, the PCAL6524 I/O expander at 0x22 on I2C4. It never answers. `[V]`
- U17 is also the only thing that can switch on the panel's 3.3 V on J16. The pull-ups that would
  turn that rail on by default are not fitted (DNP). `[R]`
- So **no software fix can light the panel** while U17 is silent.
- The fault is **not** what the 2026-09-15 notes concluded ("the I2C lines are held low"). That
  conclusion came from a measurement that doesn't work on this SoC (§4). `[V]`
- The SoC side of both buses is healthy:
  - the lines idle high;
  - the pads can drive them;
  - a hand-clocked, textbook I2C transaction goes out.
- **But no device on I2C3 or on I2C4 acknowledges any address.** `[V]`
- What remains is the board between the SoC and those chips, or the chips themselves: the meter
  checks in §6.

## 1. Behaviour

| Where | What it shows | |
|---|---|---|
| U-Boot | `pca953x gpio@22/@20: Error reading output register`, `tcpc_init: Can't find device id=0x52/0x50`, `netc_phy_rst lookup gpio@20_x failed` | `[V]` |
| Linux | `pca953x 2-0020 / 3-0022: failed writing register: -110`, `tcpci 2-0050: -110` | `[V]` |
| Linux | `imx95-dpu: failed to attach bridge to encoder for stream1: -517` every few seconds; no `/dev/dri` | `[V]` |

The deferred-probe list (`/sys/kernel/debug/devices_deferred`) gives the chain:

```
3-0022 (PCAL6524, I2C4)           probe -110 / -6
 ├ regulator-dcdc-5v              waits on 3-0022
 │  └ regulator-dcdc-3v3          waits on regulator-dcdc-5v
 │     └ 1-0045 (panel ATTINY)    waits on regulator-dcdc-3v3
 │        ├ panel-rpi-7inch       waits on 1-0045
 │        └ mipi-dsi 4acf0000.dsi.0 (TC358762)   waits on 1-0045
 │           └ imx95-dpu: bridge for stream1 -517  →  no DRM device, no UI
 ├ regulator-exp-1v8 / -exp-3v3 / -exp-5v / -ext-12v / -vbus / -m2-pwr / can1/2-stby
 └ 42550000.spi, usdhc3_pwrseq
2-0020 (PCAL6416, I2C3)           probe -110 / -6
 └ regulator-m2-mkey-1/2-pwr; Ethernet PHY resets (netc_phy_rst) → explains the missing network
```

## 2. The hardware path `[R]`

From the netlist `frmd-imx95-pro/.../SCH-95794_B1/allegro/pstxnet.dat`, the BOM `BOM-95794_B1.xls` and UM12527 Table 9:

| Bus, SoC balls | Pull-ups | Devices (series part) |
|---|---|---|
| I2C3: GPIO_IO28 U51 SDA, GPIO_IO29 V44 SCL | R191/R198 2.2k → VDD_3V3 | U7 PCAL6416 0x20 (R36/R39 0R), U44 PTN5110 0x52 (0R), U45 PTN5110 0x50 (0R), U53 NX20P3483 0x71 (0R), J18 camera (4.7k) |
| I2C4: GPIO_IO30 V46 SDA, GPIO_IO31 V48 SCL | R199/R200 2.2k → VDD_3V3 | U17 PCAL6524 0x22 (R186/R187 0R), J6 pins 1/2 (R625/R626 0R), U29 LSF0204 → M.2 Key-M SMBus (0R), J12 (0R), U23 IT6263 port (4.7k) |

The panel's supply:

```
J16 pin 22 ← FB7 ← VCC_DCDC_3V3 ← U62 MP2147 ← VCC_DCDC2_5V ← U6 MP8759 ← VBUS_IN
U62 EN = DCDC3V3_PWREN ← U17 (R59, R195); R193 10k to GND; R194 (1k pull-up) DNP  → off unless U17 drives it
U6  EN = DCDC5V_EN     ← U17 (R607);      R75 (10k pull-up) DNP                    → off unless U17 drives it
```

Other supplies:
- **The expanders' supply:** U7 and U17 run from VDD_3V3, the same rail as NVCC_GPIO and NVCC_CCM_DAP. The JTAG pads on that rail work.
- **Their reset:** U7 RESET (R32) and U17 RESET (R183) both come from `POR_B_3V3`. U30 (74AUP1G07) buffers that net from `POR_B_1V8`, and it also resets U61, the System Manager's PCAL6408 on I2C1.

## 3. Checks, in the order they were made

All the register work below was done from the UART shell or the SM monitor (§5 of [02](02-linux-without-a-console.md)).

| # | Check | Result | Meaning |
|---|---|---|---|
| 1 | GPIO2 PDIR with the pads in LPI2C mode (ALT1 + SION) | bits 28–31 = 0 | *looked like* "held low", see §4 |
| 2 | LPI2C2 (i2c-1, AON), one read of 0x45 | EIO in <10 ms (a NACK) | that controller and bus work |
| 3 | LPI2C3/4: MCR, MSR, MFSR with clocks forced on | MEN=1, MBF=1, TX FIFO 5 deep and stuck, no NDF/ALF/FEF | the master never gets past the START |
| 4 | MCR.DBGEN=1 | no change | not a debug freeze |
| 5 | master reset, one START+addr+STOP by hand | still stuck after 1 s | the same, reproducibly |
| 6 | pin-low timeout armed (MCFGR3.PINLOW=0x100, 2.7 ms) | PLTF never sets in 600 ms | the master's counters don't move |
| 7 | the same sequence on LPI2C2 | completes (ALF from the zeroed dividers), not busy | the sequence is valid; LPI2C3/4 are the odd ones |
| 8 | SM `clock.r`, CCM CLOCK_ROOT88/89 | lpi2c3/4 on, 24 MHz, OSC_24M /1 | the clock root is fine |
| 9 | input select (daisy) 0x500–0x50c | 1 = GPIO_IO29/31, GPIO_IO28/30 (RM) | correct |
| 10 | pads → **GPIO input** (mux ALT0, SM `mm`) | PDIR bits 28–31 = **1** | the lines idle high |
| 11 | the same with the internal **pull-down** on (conf 0xd9e) | still 1 | the 2.2k pull-ups are there: the balls connect |
| 12 | pads as GPIO, drive SCL4 low via PDDR | reads 0, then 1 on release | the pads drive the line |
| 13 | both buses as **i2c-gpio** (`I2CGPIO=1 board/mkdtb.sh`) | pca953x 2-0020/3-0022: **-6 (NACK)** in ~1 ms | no more timeouts: the bus works, nobody answers |
| 14 | single reads 0x20, 0x50, 0x52, 0x71 (i2c-2), 0x22 (i2c-3) | all NACK | |
| 15 | `i2cdetect -r` on both buses | nothing at any address | |
| 16 | one transaction clocked by hand at ~1 kHz (`board/i2cbang.sh 30 31 0x22`) | START ok, 8 bits ok, SCL rises every time, **SDA=1 in the ACK slot** | a valid transaction, no acknowledge |
| 17 | `POR_B_3V3` (the expanders' reset): the SM initialises U61 on that net first and stops on failure | the SM runs, so U61 answered | `POR_B_3V3` is high; U7/U17 are out of reset `[R]+[V]` |

What is left: every device on two separate buses is silent, the SoC side of both buses works, and the expanders are powered and out of reset as far as the board's logic goes. **The break is between the SoC-side nodes (the pull-ups) and the chips, or the chips are damaged.** The record of 2026-09-15 says it started after the debug USB cable was pulled from the running board and the power adapters were swapped. Before that, the same software read the PTN5110 IDs (Vendor 0x1fc9 Product 0x5110).

## 4. Correction to 2026-09-15: GPIO PDIR doesn't see a pad in another mux mode

- The old conclusion "the I2C3/I2C4 lines are held LOW" rested on GPIO2 PDIR bits 28–31 reading 0
  with the pads in LPI2C mode and SION set.
- On the i.MX95 that reading means nothing. The working LPI2C2 pads read 0 in GPIO1 PDIR in exactly
  the same way (mux 0x10, SION), while that bus answers. `[V]`
- In GPIO mode the I2C3/I2C4 lines read 1 (check 10). `[V]`
- **Lesson: on this SoC, read a pad through its GPIO only when it is muxed as GPIO.**

## 5. The LPI2C3/LPI2C4 hang (still open)

With no device answering, an LPI2C master should report NDF (NACK) at once. LPI2C3/4 instead stop
with MBF set, before or during the address byte, and the pin-low timeout never fires (checks 3–9).
- Everything I could read is correct: the clock root, the daisy select, the pad config and the module reset.
- One possibility: the Q-channel status (`BLK_CTRL_WAKEUPMIX` `QACCEPT_STATUS_0` bit 9/10) read
  1/0 for LPI2C3/LPI2C4 while both were stuck, so it doesn't explain both. `[V]`
- The `IPG_DEBUG_CA55Cn` masks are 0xfbf7ffff, so halting an A55 freezes almost every WAKEUPMIX peripheral. That's worth knowing for JTAG work, but no core was halted here. `[V]`
- It may share a cause with §3, or not: it needs the same board-level answer first.

## 6. Hardware checks to do with a meter (in order)

Unpowered:
1. J6 pin 1 ↔ U17 pin 29 (SCL) and J6 pin 2 ↔ U17 pin 30 (SDA): 0 Ω through R186/R187.
2. J6 pin 1/2 ↔ GND: not low (a shorted device input shows up here).
3. The same for I2C3: R39/R36 to U7, R337/R330 to U44, R343/R346 to U45, R344/R347 to U53.

Powered (board running Linux):

4. J6 pin 1/2: 3.3 V idle. While `i2cdetect -r -y 3` runs on the i2c-gpio DTB, the pins should be seen toggling (a scope is better).
5. U17 pin 27/31 (VDD) = 3.3 V; U17 pin 28 (RESET) = 3.3 V; the same on U7 (pins 21/23, 24).
6. TP53 (`VCC_DCDC2_5V`): 0 V expected while U17 is silent. J16 pin 22: 0 V.
7. Look for damage around U7, U17, U44, U45, U53 and the USB-C power path (U47 NX20P0407, U6).

## 7. Workarounds

- **To light the panel without U17** (a rework):
  - Fit R75 (10k, 0402) and R194 (1k, 0201). Both bucks then come up by default.
  - Then a DT that doesn't make the ATTINY (1-0045) wait for `regulator-dcdc-3v3`. That means
    dropping its `vcc-supply`, or making the dcdc regulators plain fixed ones. That's a
    `board/mkdtb.sh` option still to write.
  - The panel then also needs its 5 V on J17 pin 2/4, as in the 2026-09-15 bring-up.
  - Caveat: the probe-order race found on 2026-09-15 (ATTINY before the rail) needs the rail up at
    boot, and with R194 fitted it is.
- **For the I2C buses:** `I2CGPIO=1 board/mkdtb.sh` runs both as i2c-gpio on the same pads. It
  takes the controllers out of the picture: useful for testing, and as a fallback if §5 turns out to be the SoC.

## 8. Lessons

1. **Follow the deferred-probe chain to its root before debugging a display.** Here the DPU's
   `-517` was six suppliers away from the cause.
2. **Prove the measurement before using it** (§4). A known-good bus is the control.
3. **Split the bus from the controller.** GPIO input, then the pull-down trick, then GPIO drive,
   then i2c-gpio, then a hand-clocked transaction. Each step removes one suspect and costs one register write.
4. **The SM monitor is a second, independent console** with the rights the A55 lacks (pad mux,
   clock roots, BLK_CTRL). Every write here was undone at once and logged.
