# FRDM-IMX95-PRO: pictures for the JTAG work

Taken from NXP's design files in `../FRDM-IMX95-PRO-DESIGNFILES/` (schematic SPF-95794 rev B1, layout 95794 B1, classification Public). Each picture carries its own caption.

All of them were checked against the netlist (`Schematic/SCH-95794_B1/allegro/pstxnet.dat`). The coordinates come from the IPC-356 file in `Layout/GRB-95794_B1.zip`.

## The pictures

| # | Picture | What to take from it |
|---|---|---|
| 01 | [block diagram](01-block-diagram.png) | the debug USB-C (J22) goes to a CH9114F (four UARTs); the M.2 Key-E (J8) holds the Wi-Fi/BT card whose UART shares the JTAG pads |
| 02 | [power tree](02-power-tree-dap-voltage.png) | **the table is wrong about the JTAG pads**: it puts NVCC_CCM_DAP on 1.8 V, but balls AC23/AD24 are on VDD_3V3 in the netlist. The pads are 3.3 V |
| 03 | [JTAG/SWD at the SoC](03-jtag-swd-at-the-soc.png) | each DAP pad goes to its test point through a DNP 0R, and to the BT UART through a fitted 49.9R. D3 and R128 are DNP |
| 04 | [test points and buttons](04-jtag-testpoints-and-buttons.png) | pull-ups on the test-point side; VTREF = VDD_3V3 through 100R; **K1 = SYS_RST_B** (a reset you can wire to); hold K2 and press K1 to boot from the TF card |
| 05 | [BT UART level shifter](05-bt-uart-level-shifter.png) | U57 (LSF0204, always enabled) adds 4.7k pull-ups to 3V3 on every JTAG line (harmless); a fitted M.2 card drives TDI and TCK |
| 06 | [boot mode](06-boot-mode.png) | SW4 = BOOT_MODE[3:0]: x001 USB download, x010 eMMC, x011 SD, x100 FlexSPI NOR |
| 07 | [debug USB](07-debug-usb-uarts.png) | CH9114F UART0 = A55, UART1 = M33 (System Manager), UART2 = M7. **Its DTR/RTS pins are all unconnected**; there is no on-board probe |
| 08 | [top, the JTAG corner](08-top-jtag-corner.png) | the seven test points and the four links to fit, on the assembly sheet |
| 09 | [bottom, the BT resistors](09-bottom-bt-resistors.png) | R411/R420/R428/R434 sit **exactly under** R104/R125/R132/R135 |
| 10 | [top overview](10-top-overview.png) | where the TPs, links, K1, K2, SW3, SW4, J22, J11 (power), J1 (TF) and U25 are |
| 11 | [bottom overview](11-bottom-overview.png) | J8 (the M.2 card), the BT resistors with U57, U67 (CH9114F), TP145–148 (debug UART test points) |
| 12 | [top copper](12-top-copper-jtag.png) | the real pads from the Gerbers: TP pads labelled with their signal; the link pads, SoC side and TP side |
| 13 | [bottom copper](13-bottom-copper-bt.png) | the BT resistors' pads, seen from below |

## What the pictures add to the JTAG README

These go beyond what the JTAG kit's [README](../../../../16.frdm-imx95-pro-jtag/README.md) already says.

1. **One rework instead of two.** Move the four 49.9R resistors from the bottom to the top: R411 → R104 (TMS), R420 → R125 (TCK), R428 → R132 (TDO), R434 → R135 (TDI).
   - Same 0402 footprint, same X/Y.
   - JTAG gets a 49.9R series resistor (fine), and the BT UART is cut off for good.
   - No new parts needed. Put them back to restore Bluetooth.
2. **The test points:**
   - pads 0.89 mm, 1.52 mm apart (not a standard header pitch);
   - top row: TP1 TMS, TP2 TDO, TP3 RST;
   - bottom row: TP4 TCK, TP5 TDI, TP6 VTREF, TP7 GND;
   - the silkscreen labels RST and GND.
3. **A reset line without D3:** K1's pad 3 is SYS_RST_B (K1 pulls it to GND). A wire from it to the CH347F's TRST pin, driven open drain in SWD mode, works as SRST. Fitting D3 (NSR0320) does the same at the TP group.
4. **The power table's 1.8 V is wrong.** The pads are on VDD_3V3, so keep the CH347F at 3.3 V.
5. **The DTR warning doesn't apply to this board.** `PRO.md` warns that a DTR pulse resets it into serial download, but the CH9114F's modem pins are not connected on the Pro; `ubctl.py` says that behaviour comes from the sibling boards. Keeping DTR low costs nothing.

## How they were made

`make_pictures.py` takes the renders below and writes the PNGs:
- schematic pages: `pdftoppm -r 300`;
- the layout sheets (`Layout/DNP-95794_B1.pdf`, page 1 = bottom, page 2 = top): `pdftoppm -r 1000`, label positions from `pdftotext -bbox`;
- the Gerbers: `gerbv -x png -D 1200 -B 0`, one call per side (L1_PS + pss, L10_SS + sss).

Pixel (x, y) = ((X + 0.2054) · 1200, (6.1317 − Y) · 1200) for board coordinates X, Y in inches, the same frame as the IPC-356 file.
