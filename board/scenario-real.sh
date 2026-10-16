#!/bin/sh
# scenario-real.sh: a few seconds of real system activity to trace (docs/04 §8). The board's own
# tools from the eMMC root, nothing pinned: the scheduler places the work. Run it on the board's
# console, then start the trace from the PC within a second or two (ocd.sh trace-all):
#   sh scenario-real.sh            (prints 'started' and returns; the work runs ~5 s in the background)
#   ./ocd.sh trace-all 0xf1000000 0x1000000 1500     (JTAG_KHZ=30000 for a quick start)
# Afterwards, on the board: sh scenario-real.sh done   (stops the PID collector, saves the counters)
#   /tmp/pidmap.txt   'PID NAME' while it ran (/proc/*/comm every 0.2 s): mksnap.py --pids
#   /tmp/irq0.txt, /tmp/irq1.txt   /proc/interrupts before and after: a check on the trace
if [ "$1" = done ]; then
	kill "$(cat /tmp/pm.pid)" 2>/dev/null
	cat /proc/interrupts > /tmp/irq1.txt
	sort -n -u /tmp/pidmap.txt > /tmp/pidmap.sorted && mv /tmp/pidmap.sorted /tmp/pidmap.txt
	echo "$(wc -l < /tmp/pidmap.txt) pids in /tmp/pidmap.txt"
	exit 0
fi
cat /proc/interrupts > /tmp/irq0.txt
rm -f /tmp/pidmap.txt
sync; echo 3 > /proc/sys/vm/drop_caches          # so that reads go to the eMMC
( while :; do
	for p in /proc/[0-9]*; do read c < "$p/comm" && echo "${p#/proc/} $c"; done 2>/dev/null
	usleep 200000
  done >> /tmp/pidmap.txt ) &
echo $! > /tmp/pm.pid
( find /usr -type f | xargs sha256sum > /dev/null ) &                  # ext4, block layer, eMMC IRQs
( usleep 1000000; journalctl -b --no-pager > /dev/null ) &              # journald's files
( usleep 1500000; udevadm trigger --action=change; udevadm settle ) &   # udevd re-runs its rules
( usleep 1800000; systemctl daemon-reload ) &                           # PID 1 reloads its units
( i=0; while [ $i -lt 40 ]; do logger -t tracedemo "tick $i"; i=$((i+1)); usleep 100000; done ) &
echo started
