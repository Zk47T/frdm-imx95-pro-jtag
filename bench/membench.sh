#!/bin/bash
# membench.sh: AHB-AP read speed and correctness through the CH347F, per configuration.
# Board running U-Boot from RAM (SDP + uuu ramboot.lst): 256 KiB of U-Boot's relocated code at
# 0xfff00000 is read twice per configuration and both reads are compared with a reference read
# at 1.875 MHz. "OK" means three identical reads; anything else is a link that lies.
#   bench/membench.sh [SIZE]          default 0x40000
# Configurations: JTAG at 1.875 ... 60 MHz with OpenOCD's default 255 idle clocks per MEM-AP
# access ("memaccess"), then 8 and 0 idle clocks at 30 MHz, then SWD at 1 and 5 MHz.
set -o pipefail
H=$(cd "$(dirname "$0")/.." && pwd)
R=${ELB1:-/home/zk47/Project/5.elb1-t113}
OCD=${OCD:-$R/11.tmp/openocd-build/src/openocd}
TCL=${TCL:-$R/1.repo/3.other/openocd/tcl}
SIZE=${1:-0x40000}
ADDR=0xfff00000
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
LOG=$H/logs/membench-$(date +%Y%m%d-%H%M%S).txt

run() {  # name transport khz memaccess
	local name=$1 tr=$2 khz=$3 ma=$4 probe
	if [ "$tr" = swd ]; then
		probe=(-c 'adapter driver ch347' -c 'adapter usb vid_pid 0x1a86 0x55dd 0x1a86 0x55de' -c 'transport select swd')
	else
		probe=(-f "$R/6.host-tools/jtag-ch347/ch347.cfg")
	fi
	local t0 t1
	t0=$(date +%s.%N)
	(cd "$H" && timeout 600 "$OCD" -s "$TCL" -s "$H" "${probe[@]}" -c "adapter speed $khz" -c 'set IMX95_A55 1' \
		-f imx95-pro.cfg -f imx95-jtag.tcl -c 'catch init' -c 'imx95.ahb arp_examine' \
		-c "imx95.dap apsel 0" -c "imx95.dap memaccess $ma" -c 'targets imx95.ahb' \
		-c "set t0 [clock milliseconds]; dump_image $W/$name.a $ADDR $SIZE; echo \"T1 [expr {[clock milliseconds]-\$t0}]\"" \
		-c "set t0 [clock milliseconds]; dump_image $W/$name.b $ADDR $SIZE; echo \"T2 [expr {[clock milliseconds]-\$t0}]\"" \
		-c shutdown) > "$W/$name.log" 2>&1
	local ms1 ms2 res kib
	ms1=$(sed -n 's/^T1 //p' "$W/$name.log"); ms2=$(sed -n 's/^T2 //p' "$W/$name.log")
	if [ -z "$ms1" ] || [ ! -s "$W/$name.a" ]; then
		res="FAILED ($(grep -m1 -E 'Error' "$W/$name.log"))"; kib=-
	else
		if [ "$name" = ref ]; then cp "$W/ref.a" "$W/REF"; fi
		if cmp -s "$W/$name.a" "$W/REF" && cmp -s "$W/$name.b" "$W/REF"; then res=OK
		else res="MISMATCH: $(cmp -l "$W/$name.a" "$W/REF" | wc -l)+$(cmp -l "$W/$name.b" "$W/REF" | wc -l) bytes differ"; fi
		kib=$(( (SIZE / 1024) * 1000 / ((ms1 + ms2) / 2) ))
	fi
	printf '%-14s %-4s %6s kHz  memaccess %-3s  %8s KiB/s  %s\n' "$name" "$tr" "$khz" "$ma" "$kib" "$res" | tee -a "$LOG"
}

echo "AHB-AP reads of $SIZE bytes at $ADDR, twice per configuration, $OCD ($(date))" | tee "$LOG"
run ref       jtag 1875  255
if [ -n "$SWDONLY" ]; then
	for m in 8 4 2 1 0; do run swd-5M-m$m swd 5000 $m; done
elif [ -n "$QUICK" ]; then
	for c in "jtag-30M jtag 30000 255" "jtag-30M-m8 jtag 30000 8" "jtag-60M-m8 jtag 60000 8" "jtag-60M-m32 jtag 60000 32" "swd-5M-m8 swd 5000 8"; do run $c; done
else
	for k in 7500 15000 30000 60000; do run jtag-$k jtag $k 255; done
	run jtag-30M-m8 jtag 30000 8
	run jtag-30M-m0 jtag 30000 0
	run jtag-60M-m8 jtag 60000 8
	run swd-1M  swd 1000 255
	run swd-5M  swd 5000 255
	run swd-5M-m8 swd 5000 8
fi
echo "log: $LOG"
