# 07 — What the CH347F can do here, the M7 over the same port, and how a real probe differs

**Tags:** `[V]` measured 2026-10-16 · `[?]` not verified · `[W]` from a web source (listed at the end).

## 1. The CH347F, measured on this board

### Link `[V]`

| | Result |
|---|---|
| JTAG TAP (BYPASS + IDCODE, `jtag-linktest.py`) | 0 errors at 469 kHz … **60 MHz** (every clock the chip has: 60 MHz / 2ⁿ) |
| SWD (`SWD=1 ocd.sh dp`) | DPIDR `0x6ba02477`, the normal DP, at 1 and 5 MHz (the driver's SWD maximum) |
| memory reads, every configuration below | three identical 256 KiB reads of U-Boot each: **no silent corruption at any clock** |

### Memory read speed, AHB-AP, 256 KiB (`bench/membench.sh`) `[V]`

| Transport, clock | Idle clocks per access (`memaccess`) | KiB/s |
|---|---|---|
| JTAG 1.875 … 60 MHz | 255 (OpenOCD default) | 15 (69 with `ocd-fast`'s idle-run change) |
| JTAG 30 MHz | 8 | 103–107 |
| JTAG 60 MHz | 8 | 105–110 |
| JTAG 30 MHz | 0 | 27 (the AP answers WAIT; retries cost more) |
| SWD 1 MHz | 255 | 12 |
| SWD 5 MHz | 8 | 245–252 |
| **SWD 5 MHz** | **2** | **276** |
| SWD 5 MHz | 1 or 0 | fails loudly (`Failed to read memory`), never silently |

What limits it:
- **JTAG is bound by the CH347's bit mode, not the clock** (60 MHz is no faster than 30). Every DP/AP
  access needs TAP state moves (TMS), and the CH347 can only send those as 2-byte-per-clock bit
  operations that its firmware processes slowly.
- **SWD runs whole transactions inside the CH347** (its SWD command). At 5 MHz a 32-bit read is about 54
  bit times plus idle, roughly 11 µs, so the wire limit is about 370 KB/s, and 276 KiB/s is close to it.
- **For bulk reads (trace buffers): SWD, 5 MHz, `memaccess 2`.** A 16 MB buffer takes about 60 s.

### The chip's other interfaces (driver source, `11.tmp/ocd-fast/src/jtag/drivers/ch347.c`) `[V]` for what the driver contains

- **GPIO0–GPIO3 are free on the CH347F** (GPIO4–7 are TCK/TDO/TDI/TMS). GPIO3 is the TRST pin. In SWD mode it can drive **SRST**; `ocd-fast` also has the CH347F's dedicated SRST output in JTAG mode. Wired to K1 pad 3 (SYS_RST_B), that gives a hardware reset line, as [01](01-jtag-bringup.md) §5 notes.
- **Two UARTs** (`ttyACM4/5` here): enough for SWO capture on TDO in SWD mode, or as a third board console.
- **I2C and SPI masters on separate pins**, usable at the same time as JTAG (the F has no mode pins).
  - The useful experiment for [03](03-dsi-and-the-i2c-fault.md): put the CH347F's I2C on **J6 pins 1/2 (I2C4) + GND**, with the board on the i2c-gpio DTB (pads released), and probe 0x22 from the PC.
  - If U17 answers the CH347F, the fault is on the SoC side of the bus; if not, it is U17 or its own connection. `[?]` not wired yet.
- **I/O voltage follows VIO** (3.3 V here). There is no target-voltage sensing or adaptive level shifting.

## 2. The M7 through the same port `[V]`

| Check | Result |
|---|---|
| AP 2 IDR | `0x84770001` (AHB-AP); AP 3 (M33) the same |
| `imx95.m7 arp_examine` | "Cortex-M7 r1p2 processor detected", 8 breakpoints, 4 watchpoints; CPUID `0x411fc272` |
| running state | DHCSR `0x1010001`; DWT PCSR (`0xE000101C`) samples `0x2520`, `0x24b6` while it runs: **PC sampling without halting**, as on the A55 |
| halt | PC `0x00002450`, SP `0x200086a8`, LR `0x24bb`, xPSR `0x41000000`; VTOR 0 (code in ITCM, stack in DTCM; vector table SP `0x2001e000`, reset `0x112d`) |
| **gdb** (`gdb-multiarch`, `target extended-remote :3334`) | registers, `x/6i $pc` (Thumb-2), memory, `monitor`, detach |
| resume | DHCSR back to `0x1010001`, PC moving |

**Traps found doing this:**
- **Select the target before halt/resume** (`targets imx95.m7`). Without it, `resume` goes to the default target (A55 core 0) and the M7 stays halted.
- **Poll after examine** (`imx95.m7 arp_poll`): until then OpenOCD reports `unknown` and refuses to resume.
- **Wrap reads in `catch` in one-shot scripts:** an error (like the register name `xPSR`, which is `xpsr`) ends OpenOCD before its `resume`.
- **Halting the M7 freezes WDOG5 (bit 26) and LPTMR2 (bit 19):** `IPG_DEBUG_CM7` = 0x04080000 (RM, BLK_CTRL_WAKEUPMIX). That's its watchdog and timer, so a breakpoint doesn't trigger a watchdog reset. A halted A55 core freezes almost every WAKEUPMIX peripheral (0xfbf7ffff; [03](03-dsi-and-the-i2c-fault.md) §5).

### MCUXpresso and other IDEs

- An IDE debugs through a **GDB server**. OpenOCD with this kit is one: in `imx95-pro.cfg` with `IMX95_A55=1`, **port 3333 = A55 (SMP, threads), 3334 = M7, 3335 = M33**.
- The CH347F is not among MCUXpresso's own probes (LinkServer, J-Link, PEmicro). So:
  - **MCUXpresso IDE (Eclipse):** a "GDB Hardware Debugging" launch, with gdb = `arm-none-eabi-gdb`, remote `localhost:3334`, and "load image" if it should load the ELF into TCM. `[?]` the exact launch type in the installed IDE version.
  - **VS Code:** Cortex-Debug with `"servertype": "external"`, `"gdbTarget": "localhost:3334"` (or `"servertype": "openocd"` with this kit's cfg files). `[?]` not tried.
  - **Another PC:** `ssh -L 3334:localhost:3334 <this PC>`, or OpenOCD `bindto 0.0.0.0`.
- Why not a GDB server on the board's Linux: the A55 has no path to AP 2. Only the debug port reaches the M7's debug logic, and even the CoreSight blocks that are memory-mapped for the A55 are fenced on this BSP ([04](04-arm-trace-into-dram.md) §2). A board-hosted server needs the probe plugged into the board's own USB and an ARM64 OpenOCD, and a working Ethernet (not on this board at the moment).
- **The M7 is managed by the System Manager** (logical machine 1). It runs whatever the boot container put in its TCM. **Attach, don't reset:** SEGGER's page says the same for J-Link, "Core reset is not performed" `[W]`. New code: halt, `load` the ELF into TCM, set PC/SP from its vector table, continue.

## 3. A real J-Link, TRACE32 and this kit

| | CH347F + OpenOCD (this kit) | SEGGER J-Link | Lauterbach TRACE32 |
|---|---|---|---|
| Where the DAP logic runs | on the PC; every access is a USB round trip | in the probe's firmware: retries, WAIT handling and block transfers stay local | in the probe |
| Memory read | 105 KiB/s JTAG, 276 KiB/s SWD `[V]` | download "up to 4 MB/s" into RAM (SEGGER, high-end models) `[W]` | similar class `[?]` |
| JTAG/SWD clock | 60 MHz JTAG on the wire `[V]`, but bound by bit mode; SWD 5 MHz | 15 MHz (BASE) to 50+ MHz on the higher models `[W]` | high |
| Target voltage | fixed by VIO jumper | sensed (VTref), 1.2–5 V | sensed |
| i.MX95 support | this kit: A55 (6, SMP), M7, M33 targets, trace, mailbox; all hand-made `[V]` | listed for 6×A55, M33, M7; attach only, "Core reset is not performed"; A55 needs the bootloader to enable it `[W]` | NXP's own team uses it for the A55s (NXP forum) `[W]` |
| Trace on i.MX95 | ETR into DRAM, decoded with OpenCSD, own tools ([04](04-arm-trace-into-dram.md), [08](08-trace-visualisation.md)) | nothing specific on SEGGER's page `[W]`; on-chip ETB/ETR support in Ozone for other parts `[?]` | on-chip trace (ETR) analysis with the CTS/trace views `[?]` for this chip |
| Price | about 15 USD | about 400–1,500+ USD | thousands |

What the money buys:
- **speed and robustness** (local DAP firmware, sensed voltage, a reset line);
- **vendor device support** (scripts, unlocking, flash loaders);
- **with TRACE32, analysis software** (§ [08](08-trace-visualisation.md) 5).

What it doesn't buy on this chip:
- **another way into the trace.** Without a TPIU, every probe reads the same ETR buffer in DRAM.
- **access through a closed debug port.** Debug authentication is the chip's ELE, whichever probe is used.

## 4. Debugging a chip with A-cores and M-cores: when a probe is needed

| What you need | Without JTAG/SWD | With it |
|---|---|---|
| A Linux process | `gdbserver` over the network or serial | — |
| The kernel | KGDB over serial, kdump/crash, ftrace, perf; dmesg | halt any core in any state; this kit's `dmesg.py` even after a panic |
| M-core firmware that still runs | printf on its UART (ttyACM2 here), logs over RPMsg, fault handlers that dump registers, remoteproc (load, restart; coredumps on some platforms) | breakpoints, stepping, watchpoints (8/4 on this M7) |
| A hung core, a bus hang, boot code (ROM, SPL, TF-A, the SM) | almost nothing | the only way in |
| Instruction trace | through the OS's CoreSight driver, where it works (not on this BSP) | program and read the trace from outside ([04](04-arm-trace-into-dram.md)) |

**One probe, one debug port, every core:**
- Behind the DP each core is an access port: AP 1 the A55 cluster, AP 2 the M7, AP 3 the M33, AP 0 the system bus.
- The debugger opens one session per core (OpenOCD: one GDB port each).
- Cross-triggers (CTI/CTM) halt several cores together.

**On the i.MX95:**
- The System Manager must have powered and clocked a core before it can be attached.
- The ELE decides whether the port is open at all. In production it is closed; a signed authentication (DAT) reopens it.

**What vendors do:** NXP's EVKs and most FRDM boards carry an on-board probe (MCU-Link). This Pro has none (its CH9114F is UART only), hence the test-point rework.

## Sources

- OpenOCD CH347 driver (60 MHz JTAG, CH347F untested upstream): https://openocd.org/doc/doxygen/html/ch347_8c_source.html
- SEGGER, NXP i.MX 95: https://kb.segger.com/NXP_i.MX_95
- NXP community, J-Link on the i.MX95 M7 and the A55: https://community.nxp.com/t5/i-MX-Processors/Debugging-M7-Core-on-i-MX95-using-SEGGER-J-Link/m-p/2094698/highlight/true
- SEGGER J-Link speeds (RAM download, BASE model): https://www.segger.com/downloads/jlink/UM08001, https://thedebugstore.com/products/segger-j-link-base-classic
- Lauterbach, temporal forward and backward debugging (CTS): https://repo.lauterbach.com/news_560.html
