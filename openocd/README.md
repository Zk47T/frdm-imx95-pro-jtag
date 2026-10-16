# OpenOCD for the NXP i.MX 95 (FRDM-IMX95-PRO, CH347F)

OpenOCD has no i.MX 9 target and no CH347 interface file yet. These three files follow its own
`tcl/` layout, so they work with `-s`, or copied into an OpenOCD tree.

| File | What it is |
|---|---|
| [`target/imx95.cfg`](target/imx95.cfg) | the chip, for any probe: the DAP, six Cortex-A55 (SMP, threads in gdb), the M7, the M33, the AHB-AP and APB-AP memory targets; `imx95_a55`, `imx95_m7`, `imx95_fast` |
| [`interface/ch347.cfg`](interface/ch347.cfg) | WCH CH347F/CH347T |
| [`board/nxp_frdm-imx95-pro.cfg`](board/nxp_frdm-imx95-pro.cfg) | the two together: JTAG at 15 MHz, or SWD at 5 MHz with `TRANSPORT swd` |

Tested on 2026-10-16 with OpenOCD from git (`0.12.0+dev-02711`), with Linux running on the board, over JTAG and SWD. `[V]`
- **A55s:** all six examined; the SMP group halted and resumed together; gdb shows six threads, `bt` through the kernel, and `detach` resumes them.
- **M7:** examined, halted and resumed; gdb on port 3334.
- **Memory reads** with `imx95_fast`: 98 KiB/s over JTAG and 266 KiB/s over SWD. Without it, 14 KiB/s.

## Step by step

1. **The rework.** The board has no debug header or probe, and its JTAG test points aren't connected from the factory:
   - Fit 0 Ω (0402) on R104 (TMS), R125 (TCK), R132 (TDO) and R135 (TDI).
   - Wire TP1 TMS, TP2 TDO, TP4 TCK, TP5 TDI and TP7 GND to the CH347F. TP6 is VTREF (3.3 V): for a meter only.
   - The pads are shared with LPUART5 (Bluetooth). To debug while Linux runs, disable it in the device tree.
2. **The CH347F:**
   - Set the VIO jumper to 3.3 V.
   - Its JTAG pins go to TMS, TCK, TDI, TDO and GND. For SWD: SWDIO on TMS, SWCLK on TCK.
3. **OpenOCD from git** (the CH347 driver isn't in a release yet):
   ```
   git clone https://github.com/openocd-org/openocd && cd openocd
   ./bootstrap && ./configure --enable-ch347 --enable-internal-jimtcl && make -j && sudo make install
   sudo cp contrib/60-openocd.rules /etc/udev/rules.d/ && sudo udevadm control --reload   # USB without root
   ```
4. **Boot the board.**
   - The debug port is open in serial-download mode, and with U-Boot or Linux running. A closed part needs the ELE's debug authentication.
   - **Don't examine the A55s in serial-download mode:** the cluster is off, and the access port stays wedged until a power cycle.
   - With Linux, boot with `cpuidle.off=1` so all six cores stay powered.
5. **Run OpenOCD** (from this repository's root):
   ```
   openocd -s openocd -f board/nxp_frdm-imx95-pro.cfg -c init -c imx95_fast -c imx95_a55 -c imx95_m7
   ```
   - It should print `tap/device found: 0x9893801d` and `[imx95.m7] Cortex-M7 r1p2 processor detected`.
   - Ports: gdb 3333 (the A55s), 3334 (M7), 3335 (M33); telnet 4444.
6. **gdb:**
   ```
   gdb-multiarch vmlinux -ex 'target extended-remote :3333'      # info threads: one per A55
   arm-none-eabi-gdb m7.elf -ex 'target extended-remote :3334'   # the M7
   ```
   - **Port 3333 debugs the A55 cores themselves** (stop mode): U-Boot, the kernel, drivers, at any exception level. Halting a core stops everything on it.
   - **A Linux user program is a job for `gdbserver` on the board:** its addresses mean something only while that process runs on that core.
   - **The M7's firmware has no processes:** there, debugging the core is debugging the application.
   An IDE that can use an external GDB server can use the same ports, for example VS Code Cortex-Debug (`servertype: external`) or an Eclipse "GDB Hardware Debugging" launch in MCUXpresso. Not tried here.

## Other probes, other boards

- `target/imx95.cfg` has nothing CH347-specific. With another probe:
  ```
  openocd -s openocd -f interface/<probe>.cfg -c 'transport select jtag; adapter speed 10000' -f target/imx95.cfg
  ```
  Only the CH347F is tested here.
- **Variables:** `CHIPNAME` (imx95), `DAP_TAPID` (0x9893801d over JTAG, version ignored; 0x6ba02477 over SWD), `A55_DBGBASE` (0x80810000).

## What to know

- **The A55 debug base is 0x80810000 through the debug port** (core n at + n × 0x100000). The reference manual's 0x40810000 is the A55's own view.
- **The AHB-AP has 32-bit addresses:** it reads only the low 4 GB of the address map.
- **The M7 runs what the System Manager loaded:** attach, don't reset. Halting it also freezes its watchdog (WDOG5) and timer (LPTMR2).
- **There is no reset line** on the test points. A full chip reset from the debugger: MDM-AP (AP 6) `JTAG_SW_RST`, in this repository's `imx95-jtag.tcl`.
- **More in this repository:**
  - instruction trace from all six A55s into DRAM ([docs/04](../docs/04-arm-trace-into-dram.md));
  - Linux without a console ([docs/02](../docs/02-linux-without-a-console.md));
  - the probe's speed in detail ([docs/07](../docs/07-ch347f-m7-and-other-probes.md)).
