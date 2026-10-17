#!/bin/bash
# usbget.sh OUT [WAIT_S] [SERIAL]: read the trace disk that board/usbexport.sh shows on the board's
# USB-C into OUT, at the USB link's speed. SERIAL: the one usbexport.sh printed; without it, any
# such disk (right after a new export, udev may not have removed the previous one's link yet:
# 2026-10-17, dd then failed on a vanished /dev/sda). The disk is raw (no file system) and
# root:disk, hence sudo (sudo -A when SUDO_ASKPASS is set). Compare the printed sha256 with
# 'sha256sum /tmp/trace.bin' on the board.
set -eo pipefail
OUT=$1
[ -n "$OUT" ] || { sed -n '2,7p' "$0"; exit 2; }
for _ in $(seq "${2:-30}"); do
	D=$(ls /dev/disk/by-id/usb-NXP_IMX95_TRACE_${3:-}* 2>/dev/null | grep -v -- -part | head -1) || true
	[ -n "$D" ] && [ -b "$(readlink -f "$D")" ] && break
	D=
	sleep 1
done
[ -n "$D" ] || { echo "no /dev/disk/by-id/usb-NXP_IMX95_TRACE_*: usbexport.sh running? the board's USB-C on this PC?"; exit 1; }
DEV=$(readlink -f "$D")
SZ=$(( $(cat /sys/class/block/$(basename "$DEV")/size) * 512 ))
U=$(grep -l 0104 /sys/bus/usb/devices/*/idProduct 2>/dev/null | xargs -r -n1 dirname | while read -r u; do
	[ "$(cat "$u/idVendor")" = 1d6b ] && echo "$u"; done | head -1)
echo "$D -> $DEV: $SZ bytes, USB ${U:+$(cat "$U/speed") Mb/s}"
t0=$(date +%s.%N)
sudo ${SUDO_ASKPASS:+-A} dd if="$DEV" of="$OUT" bs=16M iflag=direct status=none
sudo ${SUDO_ASKPASS:+-A} chown "$(id -u):$(id -g)" "$OUT"
t1=$(date +%s.%N)
echo "read $(stat -c %s "$OUT") bytes in $(echo "$t1 - $t0" | bc) s: $(echo "$SZ / ($t1 - $t0) / 1048576" | bc) MiB/s"
echo "sha256 $(sha256sum "$OUT" | cut -d' ' -f1)"
