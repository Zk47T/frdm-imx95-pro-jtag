#!/usr/bin/env python3
"""jsh.py [-t SECONDS] 'CMD' ...: run shell commands on the board through the JTAG shell
(console.sh start must be running) and print their output. Each command is wrapped in markers
that appear whole only in its output, never in the shell's echo of the line, so a long or
wrapped echo can't be mistaken for output.
  jsh.py 'dmesg | tail -n 20'      jsh.py -t 300 'long command'
"""
import argparse, socket, sys, time

ap = argparse.ArgumentParser()
ap.add_argument('-t', type=float, default=60, help='seconds to wait for each command (60)')
ap.add_argument('-p', type=int, default=9090)
ap.add_argument('cmds', nargs='+')
a = ap.parse_args()

s = socket.create_connection(('127.0.0.1', a.p), timeout=5)
s.settimeout(0.3)


def drain(quiet=0.6):
    out, last = b'', time.time()
    while time.time() - last < quiet:
        try:
            d = s.recv(65536)
        except socket.timeout:
            continue
        if not d:
            break
        out, last = out + d, time.time()
    return out


drain()
rc = 0
for cmd in a.cmds:
    # the shell echoes "@@JC_B""EGIN@@" with the quotes; only the output has the marker whole
    s.sendall(('echo "@@JC_B""EGIN@@"; %s; echo "@@JC_E""ND $?@@"\n' % cmd).encode())
    out, end = b'', time.time() + a.t
    while b'@@JC_END' not in out and time.time() < end:
        try:
            out += s.recv(65536)
        except socket.timeout:
            pass
    text = out.decode('utf-8', 'replace').replace('\r', '')
    if '@@JC_END' not in text:
        print(text, end='')
        sys.exit('jsh: no end marker after %g s' % a.t)
    body = text[text.find('@@JC_BEGIN@@') + 12:text.find('@@JC_END')]
    code = text[text.find('@@JC_END') + 9:].split('@@')[0].strip()
    print(body.strip('\n'))
    rc = rc or int(code or 0)
    drain(0.2)
sys.exit(rc)
