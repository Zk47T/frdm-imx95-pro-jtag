#!/usr/bin/env python3
"""uartcap.py PORT LOG: log a board UART, read only, each line stamped with host time.
DTR and RTS are set low before the port opens (the Pro's CH9114F has them unconnected, but
PRO.md's warning is about the sibling boards, where a DTR pulse resets into SDP).
One reader per tty: a second one splits the stream (PRO.md §8). Stop it with kill.

On this PC with the CH347F plugged in first (2026-10-16):
  ttyACM0/1 = the CH347F's own UARTs; ttyACM2 = A55 (U-Boot, Linux); ttyACM3 = M33 SM;
  ttyACM4 = M7. The Yocto scripts' ttyACM0 default is then the wrong device.
"""
import serial, sys, time

port, log = sys.argv[1], sys.argv[2]
s = serial.Serial()
s.port, s.baudrate, s.timeout = port, 115200, 0.2
s.dtr = False
s.rts = False
s.open()
t0 = time.time()
buf = b''
with open(log, 'ab', buffering=0) as f:
    f.write(('--- %s opened %s ---\n' % (port, time.strftime('%F %T'))).encode())
    while True:
        d = s.read(4096)
        if not d:
            continue
        buf += d
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            f.write(b'[%9.3f] ' % (time.time() - t0) + line.rstrip(b'\r') + b'\n')
