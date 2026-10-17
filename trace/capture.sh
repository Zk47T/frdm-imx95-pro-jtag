#!/bin/bash
# capture.sh NAME SECS [BOARD_CMD]: a whole-system trace, start to finish (docs/04 §8.3).
#   1. all six A55s, kernel only, timestamps + PID, into the 2 GB buffer at 0x4_0000_0000, for SECS s
#      (ocd.sh trace-all), while BOARD_CMD runs on the board (default: scenario-real.sh's workload)
#      and a PID -> name collector samples /proc every 0.2 s
#   2. out over the board's USB (usbexport.sh, usbget.sh), SHA-256 checked on both sides
#   3. snapshot (mksnap.py, the newest live kernel text) and tracetool.py summary
# -> trace/etr-NAME.bin, trace/ss-NAME/ (summary.json, tasks.perfetto-trace, trace.folded)
# Needs Linux from linux.sh (cpuidle.off=1, the board/mkdtb.sh DTB), the board's USB-C on this PC.
# Env: TTY (the A55 console, /dev/ttyACM2), VICTLR (0x100201: no user code; 0x201: all),
#      SUDO_PASS (for the raw USB disk; or SUDO_ASKPASS)
set -eo pipefail
NAME=$1 SECS=${2:-6} CMD=${3:-}
[ -n "$NAME" ] || { sed -n '2,12p' "$0"; exit 2; }
H=$(cd "$(dirname "$0")/.." && pwd)
S=/home/zk47/Learning/yocto-imx95-pro/sources/meta-fastboot-imx95/scripts
TTY=${TTY:-/dev/ttyACM2}
Q() { python3 "$S/boardq.py" --port "$TTY" "$@" 2>&1 | grep -v '^\$ '; }
[ -n "$SUDO_ASKPASS" ] || [ -z "$SUDO_PASS" ] || export SUDO_ASKPASS=$S/askpass.sh
OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

# the board helpers: in the initramfs (/mnt) since 2026-10-17, else pushed to /tmp
F=$(Q 'ls /mnt/usbexport.sh /tmp/usbexport.sh 2>/dev/null | head -n 1' | grep usbexport || true)
[ -n "$F" ] || { echo "no usbexport.sh on the board (/mnt or /tmp): rebuild the initramfs (board/mkinitramfs.sh)"; exit 1; }
BDIR=$(dirname "$F")
[ -n "$CMD" ] || CMD="sh $BDIR/scenario-real.sh"

echo "== trace: $SECS s, all six A55s (VICTLR ${VICTLR:-0x100201}), while: $CMD"
(cd "$H" && JTAG_KHZ=30000 ./ocd.sh trace-all 0x400000000 0x80000000 $((SECS * 1000)) 0x8880 > "$OUT" 2>&1) &
OCD=$!
for _ in $(seq 120); do grep -q 'cs: tracing' "$OUT" && break; sleep 0.5; done
grep -q 'cs: tracing' "$OUT" || { cat "$OUT"; echo "the trace did not start"; exit 1; }
Q 'rm -f /tmp/pidmap.txt; ( while :; do for p in /proc/[0-9]*; do read c < $p/comm && echo "${p#/proc/} $c"; done 2>/dev/null; usleep 200000; done >> /tmp/pidmap.txt ) & echo $! > /tmp/pm.pid' >/dev/null
Q "$CMD" | tail -3
wait $OCD || true
ST=$(grep -o 'cs: stopped: .*' "$OUT" | head -1)
LOG=$(grep -o 'log: .*' "$OUT" | head -1 | cut -d' ' -f2)
echo "$ST"
RWP=$(echo "$ST" | grep -oE 'RWP=0x[0-9a-f]+' | cut -d= -f2)
FULL=$(echo "$ST" | grep -oE 'full=[01]' | cut -d= -f2)
[ -n "$RWP" ] || { echo "no ETR state in $LOG"; exit 1; }
Q 'kill $(cat /tmp/pm.pid)' >/dev/null || true
Q 'echo @@PIDMAP; sort -n -u /tmp/pidmap.txt; echo @@END' | sed -n '/^@@PIDMAP/,/^@@END/p' | grep -E '^[0-9]+ ' \
	> "$H/logs/trace-$NAME-pids.txt" || true
echo "== $(wc -l < "$H/logs/trace-$NAME-pids.txt") PIDs named"

if [ "$FULL" = 1 ]; then LEN=$((0x80000000)); FULLARG=--full; else LEN=$((RWP - 0x400000000)); FULLARG=; fi
L512=$(( (LEN + 511) / 512 * 512 ))
echo "== readout over USB: $LEN bytes"
EXP=$(Q "sh $BDIR/usbexport.sh 0x400000000 $L512" | grep -E 'copied|exported')
echo "$EXP"
SER=$(echo "$EXP" | grep -oE 'serial [0-9]+' | cut -d' ' -f2)
"$H/trace/usbget.sh" "$H/trace/etr-$NAME.bin" 60 "$SER" | tee -a "$OUT" | tail -2
PC=$(grep -oE 'sha256 [0-9a-f]{64}' "$OUT" | cut -d' ' -f2)
BD=$(Q 'sha256sum /tmp/trace.bin' | grep -oE '^[0-9a-f]{64}' || true)
[ "$PC" = "$BD" ] || { echo "SHA-256 differs: PC $PC, board $BD"; exit 1; }
echo "== SHA-256 equal on the board and the PC"
Q "sh $BDIR/usbexport.sh stop" >/dev/null || true

KT=$(ls -t "$H"/trace/ktext-live-*.bin 2>/dev/null | head -1)
[ -n "$KT" ] || echo "no live kernel text (ocd.sh ktext): decoding against vmlinux"
python3 "$H/trace/mksnap.py" --etr "$H/trace/etr-$NAME.bin" --base 0x400000000 --rwp "$RWP" $FULLARG \
	--log "$LOG" --configr 0x8880 --cores 0,1,2,3,4,5 --pids "$H/logs/trace-$NAME-pids.txt" \
	${KT:+--kernel-live "$KT"} --no-decode --out "$H/trace/ss-$NAME" | tail -2
(cd "$H/trace" && ./tracetool.py summary "ss-$NAME" | grep -vE '^  [0-9]+ / ')
