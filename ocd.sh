#!/bin/bash
# OpenOCD over the CH347F on the FRDM-IMX95-PRO's JTAG test points, one stage at a time.
# Run it with the board at the U-Boot prompt (README step 3).
#   ocd.sh scan              the TAP alone: IR capture and its two IDCODEs (the wiring, the pads)
#   ocd.sh dp                the DP: CTRL/STAT, the IDR of AP 0 and 1 (which DP the port reaches)
#   ocd.sh dm                the mailbox, no chip reset: resync, START_DBG_SESSION, the DP again
#   ocd.sh dm-reset          the same with a chip reset: the board reboots, the ROM serves the mailbox
#   ocd.sh dac [reset]       the authentication challenge into dac-<time>.txt (reads only)
#   ocd.sh a55 [dm|dm-reset] (the mailbox first, then) examine A55 core 0: halt, registers, resume
#   ocd.sh server [dm]       the same targets, left running for gdb on :3333
#   ocd.sh mdm [N]           after authentication: MDM-AP status, then N samples of each A55's WFI
#   ocd.sh pcs [CORE] [N]    after authentication: N samples of an A55's PC without halting it
#   ocd.sh etm-ids [CORE]    OS-unlock an A55's ETM and print its ID registers (for the trace decoder)
#   ocd.sh trace CORE BASE SIZE MS   ETM -> ETF -> ETR trace into DRAM (docs/04)
#   ocd.sh trace-all BASE SIZE MS [CONFIGR]   all six A55s into one buffer, kernel only (docs/04 §7, §8)
#   ocd.sh top [SECS] [CORES]          a whole-system profile through JTAG alone: PC + PID samples
#                                      of every A55, nothing halted (TOP_TTY=/dev/ttyACM2 for names)
#   ocd.sh read BASE SIZE FILE         memory into FILE over SWD at 5 MHz (276 KiB/s): trace readout
#   ocd.sh ktext [FILE]                the running kernel's text, for mksnap.py --kernel-live
# DBG0=0x... ocd.sh ...      another A55 debug base (default 0x80810000, measured 2026-10-16)
# SWD=1 ocd.sh ...           SWD instead of JTAG (SWDIO = TMS, SWCLK = TCK; TDO is then free
#                            for SWO). Not for 'scan'.
# JTAG_KHZ=1875 ocd.sh ...   another JTAG clock (469 kHz without it); only one that
#                            jtag-linktest.py --chip imx95 passed with 0 errors (README §10)
# Logs go to logs/<stage>-<time>.txt. The CH347 settings are the elb1 repo's: 469 kHz, VIO 3.3 V.
set -eo pipefail   # an OpenOCD failure must not hide behind tee
H=$(cd "$(dirname "$0")" && pwd)
R=${ELB1:-/home/zk47/Project/5.elb1-t113}
OCD=${OCD:-$R/11.tmp/openocd-build/src/openocd}
TCL=${TCL:-$R/1.repo/3.other/openocd/tcl}
mkdir -p "$H/logs"
LOG="$H/logs/$1-$(date +%Y%m%d-%H%M%S).txt"
if [ -n "$SWD" ]; then
	# the CH347's SWD clock tops out at 5 MHz
	PROBE=(-c 'adapter driver ch347' -c 'adapter usb vid_pid 0x1a86 0x55dd 0x1a86 0x55de'
	       -c 'transport select swd' -c 'adapter speed 1000')
else
	PROBE=(-f "$R/6.host-tools/jtag-ch347/ch347.cfg")
fi
# JTAG_KHZ: the CH347 runs 468.75 kHz x 2^k (469 938 1875 3750 7500 15000 30000); any other value
# rounds UP (1000 gives 1875). Raise it only after jtag-linktest.py shows 0 errors (README §10).
[ -z "$JTAG_KHZ" ] || PROBE+=(-c "adapter speed $JTAG_KHZ")
ocd() { (cd "$H" && "$OCD" -s "$TCL" -s "$H" "${PROBE[@]}" "$@") 2>&1 | tee "$LOG"; }
# 'catch init': a DP that never acknowledges power-up fails 'dap init', and the mailbox is
# still reachable after that
DAP=(-f "$H/imx95-pro.cfg" -f "$H/imx95-jtag.tcl" -c 'catch init')
A55=(-c 'set IMX95_A55 1' ${DBG0:+-c "set IMX95_DBG0 $DBG0"} "${DAP[@]}")
session() { case "$1" in dm) echo 'dm_session 0' ;; dm-reset) echo 'dm_session 1' ;; *) echo 'dm_status' ;; esac; }

case "$1" in
scan)
	[ -z "$SWD" ] || { echo "scan is JTAG only; over SWD start with 'dp'"; exit 2; }
	ocd -c 'jtag newtap imx95 cpu -irlen 8 -ircapture 0x01 -irmask 0x03' -f "$H/imx95-jtag.tcl" \
	    -c init -c scan_chain -c imx95_ids -c shutdown
	# OpenOCD exits 0 after "all ones"/"all zeroes" too: only an IDCODE counts
	grep -q 'tap/device found' "$LOG" || { echo "no TAP found, log: $LOG"; exit 1; } ;;
dp)
	ocd "${DAP[@]}" -c imx95_ids -c dm_status -c shutdown ;;
dm)
	ocd "${DAP[@]}" -c 'dm_session 0' -c shutdown ;;
dm-reset)
	ocd "${DAP[@]}" -c 'dm_session 1' -c shutdown ;;
dac)
	ocd "${DAP[@]}" -c "dm_dac $([ "$2" = reset ] && echo 1 || echo 0)" -c shutdown ;;
a55)
	# no 'dap info 1': its walk reads every core's debug block, and a read of a powered-off
	# one never completes and wedges the APB-AP until a power cycle (2026-10-16)
	ocd "${A55[@]}" -c "$(session "$2")" -c 'imx95.dap info 0' \
	    -c 'imx95.a55.0 arp_examine' -c imx95_fast -c halt -c 'reg pc' -c 'reg cpsr' -c resume -c shutdown ;;
server)
	ocd "${A55[@]}" -c "$(session "$2")" -c 'imx95.a55.0 arp_examine' -c 'imx95.ahb arp_examine' -c imx95_fast ;;
mdm)
	ocd "${DAP[@]}" -c mdm_status -c "mdm_idle ${2:-1000}" -c shutdown ;;
pcs)
	ocd "${A55[@]}" -c 'imx95.apb arp_examine' -c "a55_pcs ${2:-0} ${3:-2000}" -c shutdown ;;
etm-ids)
	ocd "${A55[@]}" -c 'imx95.apb arp_examine' -c "etm_ids ${2:-0}" -c shutdown ;;
trace)
	# trace CORE BASE SIZE MS: core's ETM -> ETF -> ETR into BASE..BASE+SIZE for MS ms
	# (docs/04). Linux on a board booted by linux.sh: BASE in a no-map region, e.g.
	# 0xf1000000 0x1000000 (below 4 GB: readable over JTAG) or 0x400000000 0x80000000 (2 GB)
	ocd "${A55[@]}" -c 'imx95.apb arp_examine' -c "etm_ids ${2:-0}" \
	    -c "cs_trace_start ${2:-0} ${3:-0xf1000000} ${4:-0x1000000}" -c "sleep ${5:-300}" \
	    -c "cs_trace_stop ${2:-0}" -c shutdown ;;
trace-all)
	# trace-all BASE SIZE MS [CONFIGR]: all six A55s, trace ID 0x10 + core, stopped at the sink so
	# that every core ends at the same instant (docs/04 §7). CONFIGR 0x8880 (default): timestamps and
	# the PID (CONTEXTIDR_EL2, traced as the VMID); 0x800: timestamps only
	# User code (EL0) is not traced by default (VICTLR=0x100201): it can't be decoded without the
	# programs' images, it took half the bandwidth, and without it a busy real system had 0 overflows
	# instead of 76,000 (T10/T11, docs/04 §8.3). VICTLR=0x201: trace it too (with mksnap.py --elf for
	# a static program). STALL=0x10C: ETM stall instead of overflow (imx95-jtag.tcl)
	# RANGES="LO:HI ...": trace only inside these address ranges (HI included; up to 4)
	RL=""; for r in ${RANGES:-}; do RL="$RL {${r%%:*} ${r##*:}}"; done
	ocd -c "set cs_ranges {$RL}" -c "set cs_victlr ${VICTLR:-0x100201}" -c "set cs_stallctlr ${STALL:-0x0}" "${A55[@]}" \
	    -c 'imx95.apb arp_examine' -c 'foreach c {0 1 2 3 4 5} { etm_ids $c }' \
	    -c "cs_multi_start {0 1 2 3 4 5} ${2:-0xf1000000} ${3:-0x1000000} ${5:-0x8880}" -c "sleep ${4:-1500}" \
	    -c 'cs_multi_stop {0 1 2 3 4 5}' -c shutdown ;;
top)
	# top [SECS] [CORES]: each core's PC sample register with CONTEXTIDR_EL2 (the PID) of the same
	# instant, round-robin for SECS s at 30 MHz, then trace/jtagtop.py (imx95-jtag.tcl a55_top_samples)
	OUT="$H/logs/top-$(date +%Y%m%d-%H%M%S).samples"
	ocd "${A55[@]}" -c "adapter speed ${JTAG_KHZ:-30000}" -c 'imx95.apb arp_examine' \
	    -c "a55_top_samples {${3:-0 1 2 3 4 5}} ${2:-5} $OUT" -c shutdown
	python3 "$H/trace/jtagtop.py" "$OUT" ${TOP_TTY:+--tty $TOP_TTY} ${TOP_ELF:+--elf $TOP_ELF} ;;
read)
	# read BASE SIZE FILE: through the AHB-AP, SWD at 5 MHz with 2 idle clocks per access: the
	# CH347F's fastest path (docs/07), in the ocd-fast build
	[ -n "$4" ] || { echo "read BASE SIZE FILE"; exit 2; }
	(cd "$H" && "$R/11.tmp/ocd-fast-build/src/openocd" -s "$R/11.tmp/ocd-fast/tcl" -s "$H" \
	    -c 'adapter driver ch347' -c 'adapter usb vid_pid 0x1a86 0x55dd 0x1a86 0x55de' -c 'transport select swd' \
	    -c 'adapter speed 5000' -c 'set IMX95_A55 1' -f imx95-pro.cfg -f imx95-jtag.tcl -c 'catch init' \
	    -c 'imx95.ahb arp_examine' -c 'imx95.dap apsel 0' -c 'imx95.dap memaccess 2' -c 'targets imx95.ahb' \
	    -c "dump_image $4 $2 $3" -c shutdown) 2>&1 | tee "$LOG" ;;
ktext)
	# ktext [FILE]: the kernel text as it runs, for the trace decoder. Linux patches its text at boot
	# (alternatives, LSE atomics, static keys: 0.4 % of the words here); decoded against vmlinux, the
	# trace loses returns. KTEXT_PA: /proc/iomem 'Kernel code' (nokaslr: fixed for this kernel)
	KB=${KB:-/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work/imx95_19x19_lpddr5_frdm_pro-poky-linux/linux-imx/6.18.20+git/build}
	SZ=$(aarch64-linux-gnu-readelf -lW "$KB/vmlinux" | awk '/LOAD/ {print $5; exit}')
	exec "$0" read "${KTEXT_PA:-0x90400000}" "$SZ" "${2:-trace/ktext-live-$(date +%Y%m%d).bin}" ;;
*)
	sed -n '2,23p' "$0"; exit 2 ;;
esac
echo "log: $LOG"
