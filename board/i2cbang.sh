#!/mnt/busybox sh
# i2cbang.sh SDA_BIT SCL_BIT ADDR: one I2C address probe, bit by bit through GPIO2 with devmem
# (about 1 kHz), printing what the pins read at every step: START, the 8 address bits (write),
# the ACK clock, STOP. For a bus where every device NACKs and the controller can't be trusted.
# GPIO2 (0x43810000): PDOR stays 0, so "drive low" = set the bit in PDDR, "release" = clear it;
# the line then goes high through the board's 2.2k pull-up. PDIR reads the pad (GPIO mode).
# i2c-3 (I2C4): SDA 30, SCL 31.  i2c-2 (I2C3): SDA 28, SCL 29.
B=/mnt/busybox
DDR=0x43810054
IN=0x43810050
SDA=$((1 << $1)); SCL=$((1 << $2)); ADDR=$3
lo() { $B devmem $DDR 32 $(( $($B devmem $DDR 32) | $1 )); }
hi() { $B devmem $DDR 32 $(( $($B devmem $DDR 32) & (0xFFFFFFFF ^ $1) )); }
pins() { v=$($B devmem $IN 32); echo "sda=$(( (v & SDA) != 0 )) scl=$(( (v & SCL) != 0 ))"; }
[ "$($B devmem 0x43810040 32)" = 0x00000000 ] || { echo "PDOR is not 0: stop"; exit 1; }
echo "idle:        $(pins)"
hi $SDA; hi $SCL; lo $SDA; echo "START sda lo: $(pins)"; lo $SCL
byte=$(( ADDR << 1 ))
i=7
while [ $i -ge 0 ]; do
	b=$(( (byte >> i) & 1 ))
	if [ $b = 1 ]; then hi $SDA; else lo $SDA; fi
	hi $SCL
	echo "bit $i = $b  scl released: $(pins)"
	lo $SCL
	i=$((i - 1))
done
hi $SDA
hi $SCL
echo "ACK clock:    $(pins)   (sda=0: a device acknowledged)"
lo $SCL
lo $SDA; hi $SCL; hi $SDA
echo "STOP:        $(pins)"
