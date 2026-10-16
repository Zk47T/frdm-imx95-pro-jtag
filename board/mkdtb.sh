#!/bin/bash
# The DTB for a JTAG + trace session on the DSI panel build:
#   board/mkdtb.sh [BASE.dtb]   default: the deployed imx95-19x19-frdm-pro-rpi-7inch-panel.dtb
#   -> board/out/imx95-19x19-frdm-pro-rpi-7inch-panel-jtag.dtb
# Changes against the base, each checked with fdtget at the end:
# - LPUART5 (serial@42590000) disabled: its pins are the JTAG pins, and Linux would mux them away;
# - core 0's trace path enabled: etm@40840000 -> funnel -> funnel-sys -> etf -> etr;
# - /reserved-memory/jtag@f0000000, 32 MB no-map below 4 GB (the AHB-AP reaches only the low
#   4 GB): the RTT shell's control block at +0 (64 KB), a JTAG read window from +1 MB;
# - /reserved-memory/trace@400000000, 2 GB no-map at the top of the 16 GB, given to the ETR as
#   its "tracedata" region (kernel 6.18's ETR_MODE_RESRV: buf_mode_preferred = resrv).
set -eo pipefail
H=$(cd "$(dirname "$0")" && pwd)
D=/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/deploy/images/imx95-19x19-lpddr5-frdm-pro
BASE=${1:-$D/imx95-19x19-frdm-pro-rpi-7inch-panel.dtb}
OUT=$H/out/$(basename "$BASE" .dtb)-jtag.dtb
mkdir -p "$H/out"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
dtc -q -I dtb -O dts -o "$T/in.dts" "$BASE"
python3 - "$T/in.dts" "$T/out.dts" <<'EOF'
import re, sys
s = open(sys.argv[1]).read()
ph = max(int(x, 16) for x in re.findall(r'phandle = <(0x[0-9a-f]+)>', s)) + 1

def node_span(name):
    """start of the node's body and the index of its closing brace (first match)"""
    m = re.search(r'\n(\t+)' + re.escape(name) + r' \{\n', s)
    if not m:
        sys.exit('no node ' + name)
    ind = m.group(1)
    end = s.index('\n' + ind + '};', m.end())
    return m.end(), end

def set_status(name, val):
    global s
    a, b = node_span(name)
    body = s[a:b]
    # only this node's own status line, not a child's: it is at one tab deeper than the node
    ind = re.search(r'\n(\t+)' + re.escape(name) + r' \{', s).group(1) + '\t'
    new, n = re.subn(r'(?m)^' + ind + r'status = "[a-z]+";', ind + 'status = "%s";' % val, body, count=1)
    if not n:
        new = ind + 'status = "%s";\n' % val + body
    s = s[:a] + new + s[b:]

set_status('serial@42590000', 'disabled')
import os
if os.environ.get('TRACE') == '1':
    # 2026-10-16: with these on, Linux panics at 0.2 s: a synchronous external abort reading
    # the ETF's PIDR (0x41030FE0) in amba_read_periphid. Off by default until that is solved.
    for n in ('etm@40840000', 'funnel', 'funnel-sys', 'etf@41030000', 'etr@41040000'):
        set_status(n, 'okay')

if os.environ.get('I2CGPIO') == '1':
    # 2026-10-16: LPI2C3 and LPI2C4 (WAKEUPMIX) never get past a START (master busy, FIFO
    # stuck, no NACK, no pin-low timeout) while the lines idle high as GPIOs. Run both buses
    # as i2c-gpio on the same pads to see whether the devices behind them answer.
    # GPIO2 (phandle 0x35): IO28 = I2C3 SDA, IO29 = I2C3 SCL, IO30 = I2C4 SDA, IO31 = I2C4 SCL.
    a2, b2 = node_span('lpi2c4grp')
    ind4 = '\t\t\t\t'
    grp = ''
    gph = ph            # the trace buffer keeps ph; the groups take the next two
    for name, regs in (('i2c3gpiogrp', (0x80, 0x284, 0x84, 0x288)), ('i2c4gpiogrp', (0x88, 0x28c, 0x8c, 0x290))):
        gph += 1
        grp += ('\n%s%s {\n%s\tfsl,pins = <%#x %#x 0x00 0x00 0x00 0xb9e %#x %#x 0x00 0x00 0x00 0xb9e>;\n'
                '%s\tphandle = <%#x>;\n%s};\n' % (ind4, name, ind4, regs[0], regs[1], regs[2], regs[3], ind4, gph, ind4))
        globals()[name] = gph
    end = s.index('\n' + ind4 + '};', a2) + len('\n' + ind4 + '};')
    s = s[:end] + '\n' + grp + s[end:]
    for node, sda, scl, g in (('i2c@42530000', 28, 29, i2c3gpiogrp), ('i2c@42540000', 30, 31, i2c4gpiogrp)):
        a, b = node_span(node)
        body = s[a:b]
        own = '\t\t\t\t'
        keep = []
        for line in body.split('\n'):
            p = line[len(own):] if line.startswith(own) and not line.startswith(own + '\t') else None
            if p and re.match(r'(reg|interrupts|clocks|clock-names|dmas|dma-names|clock-frequency|power-domains) =', p):
                continue
            if p and p.startswith('compatible ='):
                line = own + 'compatible = "i2c-gpio";'
            if p and p.startswith('pinctrl-0 ='):
                line = own + 'pinctrl-0 = <%#x>;' % g
            keep.append(line)
        body = '\n'.join(keep)
        body = (own + 'sda-gpios = <0x35 %d 0x02>;\n' % sda + own + 'scl-gpios = <0x35 %d 0x02>;\n' % scl +
                own + 'i2c-gpio,delay-us = <0x05>;\n') + body
        s = s[:a] + body + s[b:]

a, b = node_span('reserved-memory')
ind = '\t\t'
s = s[:b] + ('\n%sjtag@f0000000 {\n%s\treg = <0x00 0xf0000000 0x00 0x2000000>;\n%s\tno-map;\n%s};\n'
             '\n%strace@400000000 {\n%s\treg = <0x04 0x00 0x00 0x80000000>;\n%s\tno-map;\n%s\tphandle = <%#x>;\n%s};'
             % (ind, ind, ind, ind, ind, ind, ind, ind, ph, ind)) + s[b:]

a, b = node_span('etr@41040000')
if os.environ.get('TRACE') == '1':
  s = s[:a] + '\t\t\tmemory-region = <%#x>;\n\t\t\tmemory-region-names = "tracedata";\n' % ph + s[a:]
open(sys.argv[2], 'w').write(s)
print('trace buffer phandle %#x' % ph)
EOF
dtc -q -I dts -O dtb -o "$OUT" "$T/out.dts"
# check every change in the result
chk() { echo "  $2 $3 = $(fdtget -t "$1" "$OUT" "$2" "$3" 2>&1)"; }
chk s /soc/bus@42000000/serial@42590000 status
for n in etm@40840000 funnel funnel-sys etf@41030000 etr@41040000; do chk s /soc/$n status; done
chk x /reserved-memory/jtag@f0000000 reg
chk x /reserved-memory/trace@400000000 reg
chk x /reserved-memory/trace@400000000 phandle
chk x /soc/etr@41040000 memory-region
chk s /soc/etr@41040000 memory-region-names
echo "dtb: $OUT"
echo "diff against the base:"
dtc -q -I dtb -O dts "$BASE" > "$T/a.dts"; dtc -q -I dtb -O dts "$OUT" > "$T/b.dts"
diff "$T/a.dts" "$T/b.dts" | grep -E '^[<>]' | sed 's/^/  /' || true
