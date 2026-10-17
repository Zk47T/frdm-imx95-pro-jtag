#!/bin/sh
# usbexport.sh: show a trace buffer to the PC as a read-only USB disk on the board's USB-C port
# (the one it boots from in serial download). The debug port reads 266 KiB/s; this goes at the
# speed of the USB link (docs/04 §8.3).
#   usbexport.sh PHYS LEN    copy LEN bytes at physical PHYS (a no-map region) into /tmp/trace.bin
#                            (RAM) with physwin, then export that file
#   usbexport.sh file FILE   export an existing file
#   usbexport.sh stop        remove the gadget: the port is free again
# On the PC: trace/usbget.sh OUT (the disk is /dev/disk/by-id/usb-NXP_IMX95_TRACE_*; raw, no
# file system). The disk is the file rounded down to 512 bytes.
set -e
G=/sys/kernel/config/usb_gadget/imx95trace
PW=${PHYSWIN:-/mnt/physwin}

stop() {
	[ -d $G ] || return 0
	echo > $G/UDC 2>/dev/null || true
	rm -f $G/configs/c.1/mass_storage.0
	rmdir $G/configs/c.1/strings/0x409 $G/configs/c.1 $G/functions/mass_storage.0 $G/strings/0x409 $G
}

case "$1" in
stop)
	stop; echo stopped; exit 0 ;;
file)
	F=$2 ;;
*)
	[ -n "$2" ] || { sed -n '2,11p' "$0"; exit 2; }
	F=/tmp/trace.bin
	t0=$(date +%s)
	$PW r "$1" "$2" > $F
	echo "copied $(stat -c %s $F) bytes from $1 in $(( $(date +%s) - t0 )) s"
	;;
esac
stop
mkdir $G
cd $G
echo 0x1d6b > idVendor          # Linux Foundation
echo 0x0104 > idProduct         # multifunction composite gadget
mkdir strings/0x409
echo NXP > strings/0x409/manufacturer
echo "i.MX95 trace buffer" > strings/0x409/product
date +%Y%m%d%H%M%S > strings/0x409/serialnumber
mkdir functions/mass_storage.0
echo 1 > functions/mass_storage.0/lun.0/ro
echo 0 > functions/mass_storage.0/lun.0/removable
# vendor (8), product (16), revision (4): the PC names the disk usb-NXP_IMX95_TRACE_<serial>
printf 'NXP     IMX95_TRACE     0001' > functions/mass_storage.0/lun.0/inquiry_string
echo "$F" > functions/mass_storage.0/lun.0/file
mkdir configs/c.1 configs/c.1/strings/0x409
echo "trace buffer" > configs/c.1/strings/0x409/configuration
ln -s functions/mass_storage.0 configs/c.1/
ls /sys/class/udc | head -n 1 > UDC
echo "exported $F ($(stat -c %s $F) bytes) on $(cat UDC) as serial $(cat strings/0x409/serialnumber)"
