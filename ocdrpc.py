#!/usr/bin/env python3
"""ocdrpc.py 'TCL' ...: commands to the OpenOCD that console.sh started, over its Tcl RPC port
(6666), and their results. Also importable: Ocd().cmd('...'), Ocd().read32(addr, n),
Ocd().dump(addr, size) -> bytes (through the AHB-AP, cores running).
OpenOCD's Tcl RPC: a command terminated by 0x1a, the result terminated by 0x1a.
"""
import os, socket, sys, tempfile


class Ocd:
    def __init__(self, port=6666, host='127.0.0.1'):
        self.s = socket.create_connection((host, port), timeout=600)

    def cmd(self, c):
        self.s.sendall(c.encode() + b'\x1a')
        out = b''
        while not out.endswith(b'\x1a'):
            d = self.s.recv(65536)
            if not d:
                raise IOError('OpenOCD closed the Tcl port')
            out += d
        return out[:-1].decode('utf-8', 'replace')

    def read32(self, addr, n=1, target='imx95.ahb'):
        r = self.cmd('%s read_memory %#x 32 %d' % (target, addr, n))
        try:
            return [int(x, 0) for x in r.split()]
        except ValueError:
            raise IOError('read %#x: %s' % (addr, r))

    def dump(self, addr, size, target='imx95.ahb'):
        """size bytes at addr, through a temporary file (dump_image is the fast path)."""
        fd, path = tempfile.mkstemp(prefix='ocd-dump-')
        os.close(fd)
        try:
            r = self.cmd('targets %s; dump_image %s %#x %#x' % (target, path, addr, size))
            data = open(path, 'rb').read()
            if len(data) != size:
                raise IOError('dump %#x+%#x: %s' % (addr, size, r))
            return data
        finally:
            os.unlink(path)


if __name__ == '__main__':
    o = Ocd()
    for c in sys.argv[1:]:
        print(o.cmd(c))
