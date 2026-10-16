#!/bin/bash
# The JTAG initramfs: busybox, rtt-console, physwin and /init, as a gzip'd newc cpio.
#   board/mkinitramfs.sh       -> board/out/initramfs.cpio.gz
# Owned by root without root: cpio -R 0:0.
set -eo pipefail
H=$(cd "$(dirname "$0")" && pwd)
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
[ -x "$H/out/busybox" ] || "$H/build.sh"
cp "$H/out/busybox" "$H/out/rtt-console" "$H/out/physwin" "$H/init" "$H/jtagsh" "$H/jtag-env.sh" "$D/"
chmod 755 "$D/init" "$D/jtagsh" "$D/busybox" "$D/rtt-console" "$D/physwin"
mkdir -p "$D/dev" "$D/proc" "$D/sys" "$D/newroot"
(cd "$D" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet) | gzip -9 > "$H/out/initramfs.cpio.gz"
ls -l "$H/out/initramfs.cpio.gz"
