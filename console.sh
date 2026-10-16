#!/bin/bash
# The JTAG shell on the i.MX95's Linux: OpenOCD polls the RTT block that board/rtt-console keeps
# at 0xf0000000 (in /reserved-memory/jtag@f0000000) through the AHB-AP, cores running, and
# serves the shell on a TCP port. One OpenOCD owns the CH347F, so this one also keeps its Tcl
# port open for memory reads by the other tools (jsh.py, the trace scripts).
#   console.sh start      OpenOCD in the background: RTT on :9090, Tcl RPC on :6666 (log in logs/)
#   console.sh stop
#   console.sh            interactive (socat; leave with Ctrl-])
#   jsh.py 'CMD'          one command, its output (needs 'start')
# Needs the OpenOCD with the mem_ap RTT commands (T113 change 6, 11.tmp/ocd-fast-build).
set -eo pipefail
H=$(cd "$(dirname "$0")" && pwd)
R=${ELB1:-/home/zk47/Project/5.elb1-t113}
OCD=${OCD:-$R/11.tmp/ocd-fast-build/src/openocd}
TCL=${TCL:-$R/11.tmp/ocd-fast/tcl}
PHYS=${PHYS:-0xf0000000}
PORT=${PORT:-9090}
PIDF=$H/logs/console.pid
mkdir -p "$H/logs"

start() {
	[ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && { echo "running (pid $(cat "$PIDF"))"; return; }
	LOG=$H/logs/console-$(date +%Y%m%d-%H%M%S).txt
	(cd "$H" && exec "$OCD" -s "$TCL" -s "$H" -f "$R/6.host-tools/jtag-ch347/ch347.cfg" \
		-c "adapter speed ${JTAG_KHZ:-15000}" -c 'set IMX95_A55 1' -f imx95-pro.cfg -f imx95-jtag.tcl \
		-c 'gdb port disabled' -c 'telnet port disabled' -c 'tcl port 6666' -c init \
		-c 'imx95.ahb arp_examine' -c imx95_fast \
		-c "imx95.ahb rtt setup $PHYS 0x100 \"SEGGER RTT\"" \
		-c "rtt server start $PORT 0") > "$LOG" 2>&1 &
	echo $! > "$PIDF"
	for i in $(seq 60); do
		grep -q "Listening on port $PORT for rtt" "$LOG" && break
		kill -0 "$(cat "$PIDF")" 2>/dev/null || { cat "$LOG"; exit 1; }
		sleep 0.25
	done
	# 'rtt start' fails until rtt-console has written its block: retry over the Tcl port
	# (a failing -c would end OpenOCD)
	for i in $(seq "${WAIT:-120}"); do
		r=$(python3 "$H/ocdrpc.py" 'expr {[catch {imx95.ahb rtt start}] ? "FAIL" : "OK"}') || true
		[ "$r" = OK ] && { echo "RTT block found at $PHYS after ${i}s; shell on :$PORT (log $LOG)"; return; }
		sleep 1
	done
	echo "OpenOCD up, but no RTT block at $PHYS (is Linux up? rtt-console running?); retry: console.sh start; log $LOG"
}

case "$1" in
start) start ;;
stop) [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"; echo stopped ;;
*) start; socat -,raw,echo=0,escape=0x1d tcp:127.0.0.1:$PORT ;;
esac
