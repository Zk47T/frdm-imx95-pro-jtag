#!/bin/bash
# Build the board side of the JTAG shell for the i.MX95's A55 (static arm64, no libc on the
# board needed): busybox (the T113 rescue config and patch, re-targeted), rtt-console, physwin.
#   board/build.sh            -> board/out/{busybox,rtt-console,physwin}
# Sources: busybox 1.38.0 from the elb1 repo's download dir; rtt-console.c is the T113 one
# (doc 80 §13), unchanged: its RTT block holds 32-bit physical addresses, which is fine below 4 GB.
set -eo pipefail
H=$(cd "$(dirname "$0")" && pwd)
R=${ELB1:-/home/zk47/Project/5.elb1-t113}
BB=$R/6.host-tools/jtag-ch347/rtt-console/busybox
TB=${TB:-$R/11.tmp/br-dl/busybox/busybox-1.38.0.tar.bz2}
B=$R/11.tmp/imx95-busybox
X=aarch64-linux-gnu-
mkdir -p "$H/out"

if [ ! -x "$B/busybox" ]; then
	rm -rf "$B" && mkdir -p "$B"
	tar xjf "$TB" -C "$B" --strip-components=1
	(cd "$B" && patch -p1 -s < "$BB/busybox-1.38.0-rescue.patch")
	cp "$BB/busybox-1.38.0-rescue.config" "$B/.config"
	(cd "$B" && (yes "" || true) | make ARCH=arm64 CROSS_COMPILE=$X oldconfig > /dev/null)
	(cd "$B" && make ARCH=arm64 CROSS_COMPILE=$X -j"$(nproc)" busybox > build.log 2>&1) || { tail -20 "$B/build.log"; exit 1; }
fi
cp "$B/busybox" "$H/out/busybox"
${X}gcc -O2 -static -Wall -o "$H/out/rtt-console" "$H/rtt-console.c"
${X}gcc -O2 -static -Wall -o "$H/out/physwin" "$H/physwin.c"
${X}strip "$H/out/rtt-console" "$H/out/physwin"
file "$H/out/"*
