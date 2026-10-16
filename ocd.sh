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
# DBG0=0x80810000 ocd.sh ... the A55 debug base, if 'dap info 1' shows it there
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
	ocd "${A55[@]}" -c "$(session "$2")" -c 'imx95.dap info 0' -c 'imx95.dap info 1' \
	    -c 'imx95.a55.0 arp_examine' -c imx95_fast -c halt -c 'reg pc' -c 'reg cpsr' -c resume -c shutdown ;;
server)
	ocd "${A55[@]}" -c "$(session "$2")" -c 'imx95.a55.0 arp_examine' -c 'imx95.ahb arp_examine' -c imx95_fast ;;
mdm)
	ocd "${DAP[@]}" -c mdm_status -c "mdm_idle ${2:-1000}" -c shutdown ;;
pcs)
	ocd "${A55[@]}" -c 'imx95.apb arp_examine' -c "a55_pcs ${2:-0} ${3:-2000}" -c shutdown ;;
*)
	sed -n '2,16p' "$0"; exit 2 ;;
esac
echo "log: $LOG"
