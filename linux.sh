#!/bin/bash
# Boot Linux from RAM for a JTAG session: the DSI panel kernel, the JTAG DTB (board/mkdtb.sh)
# and the JTAG initramfs (board/mkinitramfs.sh), through the U-Boot fastboot gadget that
# 'uuu ramboot.lst' left running after an SDP boot. Nothing is written to the eMMC: the
# initramfs mounts the eMMC root and hands over to its own init.
#   SUDO_PASS=... linux.sh            then: console.sh start; jsh.py 'dmesg | tail'
#   EXTRA_BOOTARGS='initcall_debug' linux.sh
# The command line is the falcon DTB's own (quiet loglevel=3 ...) plus:
#   nokaslr         kernel addresses = System.map (JTAG symbol reads, trace decode)
#   cpuidle.off=1   cores only WFI, never powered down: their debug and ETM registers stay
#                   reachable over the APB-AP (an access to a powered-off block wedges it)
# No log_buf_len: the static 128 KB __log_buf stays in the kernel image, below 4 GB, where the
# AHB-AP (32-bit) can read it even if Linux never gets as far as the shell.
set -eo pipefail
H=$(cd "$(dirname "$0")" && pwd)
S=/home/zk47/Learning/yocto-imx95-pro/sources/meta-fastboot-imx95/scripts
D=/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/deploy/images/imx95-19x19-lpddr5-frdm-pro
KIMG=${KIMG:-$D/Image}
DTB=${DTB:-$H/board/out/imx95-19x19-frdm-pro-rpi-7inch-panel-jtag.dtb}
INITRD=${INITRD:-$H/board/out/initramfs.cpio.gz}
INITRD_ADDR=0x96000000
# VERBOSE=1: every kernel message on the console (ttyLP0 = the A55 UART), for a hang that leaves
# no time to read the log any other way
if [ -n "$VERBOSE" ]; then Q="loglevel=7 ignore_loglevel"; else Q="quiet loglevel=3 systemd.show_status=0"; fi
ARGS="console=ttyLP0,115200 root=/dev/mmcblk0p2 rootwait rw $Q nokaslr cpuidle.off=1 ${EXTRA_BOOTARGS:-}"
export SUDO_ASKPASS=$S/askpass.sh
[ -n "$SUDO_PASS" ] || { echo "SUDO_PASS is needed (uuu runs as root)"; exit 2; }
lsusb | grep -q '1fc9:0152' || { echo "no U-Boot fastboot gadget (1fc9:0152): SDP boot + 'uuu ramboot.lst' first"; exit 1; }
for f in "$KIMG" "$DTB" "$INITRD"; do [ -f "$f" ] || { echo "missing $f"; exit 1; }; done

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
cp -L "$KIMG" "$W/_Image"; cp -L "$DTB" "$W/_board.dtb"; cp -L "$INITRD" "$W/_initrd"
SZ=$(stat -c %s "$W/_initrd")
cat > "$W/boot.lst" <<EOF
uuu_version 1.4.149
FB: ucmd setenv fastboot_buffer \${loadaddr}
FB: download -f _Image
FB: ucmd setenv fastboot_buffer \${fdt_addr}
FB: download -f _board.dtb
FB: ucmd setenv fastboot_buffer $INITRD_ADDR
FB: download -f _initrd
FB: ucmd setenv bootargs $ARGS
FB: acmd booti \${loadaddr} $INITRD_ADDR:$(printf %x "$SZ") \${fdt_addr}
FB: done
EOF
echo "kernel  $(readlink -f "$KIMG") ($(stat -Lc %s "$KIMG") B)"
echo "dtb     $DTB"
echo "initrd  $INITRD ($SZ B) at $INITRD_ADDR"
echo "args    $ARGS"
mkdir -p "$H/logs"
cp "$W/boot.lst" "$H/logs/linux-$(date +%Y%m%d-%H%M%S).lst"
(cd "$W" && sudo -A uuu boot.lst) 2>&1 | tr '\r' '\n' | grep -vE '^\s*$|\[1F' | tail -n 5
