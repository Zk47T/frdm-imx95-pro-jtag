#!/usr/bin/env python3
"""dmesg.py [-n LINES] [-g REGEX] [-c CORE]: the running i.MX95 kernel's log, read over JTAG from printk's ring
buffer through the AHB-AP, no core halted and no software on the board needed. For when the
JTAG shell isn't up (or Linux never got that far). Needs 'console.sh start' (its Tcl port).

The 64-bit port of the T113's jtag-dmesg.py (6.host-tools/jtag-ch347): same ring walk
(kernel/printk/printk_ringbuffer.h), with 8-byte ids and state in the top 2 bits of state_var.
Addresses (boot with nokaslr; linux.sh does):
- kernel image: phys = va - _text + 0x90400000, where booti leaves the Image (checked: the
  linux_banner read there matches vmlinux);
- linear map: phys = va - PAGE_OFFSET (0xffff000000000000, 48-bit VA) + memstart_addr.
The AHB-AP has 32-bit addresses: a ring moved above 4 GB (log_buf_len=) is out of reach.
"""
import argparse, os, re, subprocess, sys, time

sys.path.insert(0, '/home/zk47/Project/5.elb1-t113/6.host-tools/jtag-ch347')
from kmem import gdb_values, offsetof            # offline gdb on vmlinux, cached
from ocdrpc import Ocd

KB = '/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work/imx95_19x19_lpddr5_frdm_pro-poky-linux/linux-imx/6.18.20+git/build'
VMLINUX = os.environ.get('VMLINUX', KB + '/vmlinux')
KIMG_PHYS = int(os.environ.get('KIMG_PHYS', '0x90400000'), 0)
TEXT = 0xffff800080000000
PAGE_OFFSET = 0xffff000000000000
ID_MASK = (1 << 62) - 1
COMMITTED, FINALIZED = 1, 2


def sym(name):
    out = subprocess.run(['grep', '-m1', ' %s$' % name, KB + '/System.map'], capture_output=True, text=True).stdout
    return int(out.split()[0], 16)


class K:
    def __init__(self, o, core=None):
        self.o, self.core = o, core
        self.memstart = None
        if core is None:
            self.memstart = self.u64(sym('memstart_addr'))

    def phys(self, va):
        if va >= TEXT:
            return va - TEXT + KIMG_PHYS
        if va >= PAGE_OFFSET and self.memstart is not None:
            return va - PAGE_OFFSET + self.memstart
        raise ValueError('%#x: not a kernel address' % va)

    def raw(self, va, n):
        if self.core is not None:       # through the halted core: its caches, its MMU
            a = va & ~3
            return self.o.dump(a, (va + n - a + 3) & ~3, target='imx95.a55.%d' % self.core)[va - a:va - a + n]
        p = self.phys(va)
        if p + n > 1 << 32:
            raise IOError('%#x (phys %#x) is above 4 GB: the AHB-AP cannot reach it' % (va, p))
        a = p & ~3
        return self.o.dump(a, (p + n - a + 3) & ~3)[p - a:p - a + n]

    def u64(self, va):
        return int.from_bytes(self.raw(va, 8), 'little')


def layout():
    L = {}
    for k, (s, m) in {'count_bits': ('printk_ringbuffer', 'desc_ring.count_bits'),
                      'descs': ('printk_ringbuffer', 'desc_ring.descs'),
                      'infos': ('printk_ringbuffer', 'desc_ring.infos'),
                      'head_id': ('printk_ringbuffer', 'desc_ring.head_id'),
                      'tail_id': ('printk_ringbuffer', 'desc_ring.tail_id'),
                      'size_bits': ('printk_ringbuffer', 'text_data_ring.size_bits'),
                      'data': ('printk_ringbuffer', 'text_data_ring.data'),
                      'sv': ('prb_desc', 'state_var'), 'begin': ('prb_desc', 'text_blk_lpos.begin'),
                      'next': ('prb_desc', 'text_blk_lpos.next'), 'seq': ('printk_info', 'seq'),
                      'ts': ('printk_info', 'ts_nsec'), 'len': ('printk_info', 'text_len'),
                      'fac': ('printk_info', 'facility')}.items():
        L[k] = offsetof(s, m)
    L['desc_size'] = 'sizeof(struct prb_desc)'
    L['info_size'] = 'sizeof(struct printk_info)'
    return type('L', (), gdb_values(L, VMLINUX))


def records(k, L):
    rb = k.u64(sym('prb'))
    hdr = k.raw(rb, L.data + 8)
    g32 = lambda o: int.from_bytes(hdr[o:o + 4], 'little')
    g64 = lambda o: int.from_bytes(hdr[o:o + 8], 'little')
    count, size = 1 << g32(L.count_bits), 1 << g32(L.size_bits)
    descs, infos, data = g64(L.descs), g64(L.infos), g64(L.data)
    head, tail = g64(L.head_id) & ID_MASK, g64(L.tail_id) & ID_MASK
    # the whole rings at once: a few round trips instead of three per record
    D = k.raw(descs, count * L.desc_size)
    I = k.raw(infos, count * L.info_size)
    T = k.raw(data, size)
    out = []
    did = tail
    while True:
        i = did % count
        d = D[i * L.desc_size:(i + 1) * L.desc_size]
        sv = int.from_bytes(d[L.sv:L.sv + 8], 'little')
        begin = int.from_bytes(d[L.begin:L.begin + 8], 'little')
        nxt = int.from_bytes(d[L.next:L.next + 8], 'little')
        if sv & ID_MASK == did and (sv >> 62) in (COMMITTED, FINALIZED):
            inf = I[i * L.info_size:(i + 1) * L.info_size]
            seq = int.from_bytes(inf[L.seq:L.seq + 8], 'little')
            ts = int.from_bytes(inf[L.ts:L.ts + 8], 'little')
            n = int.from_bytes(inf[L.len:L.len + 2], 'little')
            text = ''
            if not begin & 1:
                b, e = begin % size, nxt % size
                if begin // size != nxt // size or b >= e:
                    b = 0
                b += 8                  # the block's descriptor id (unsigned long)
                n = max(0, min(n, e - b))
                text = T[b:b + n].decode(errors='replace')
            out.append((seq, ts, text))
        if did == head:
            break
        did = (did + 1) & ID_MASK
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=0, help='only the last N lines')
    ap.add_argument('-g', help='only lines matching this regex')
    ap.add_argument('-c', type=int, metavar='CORE', help='read through this A55, halted (all six halt: one SMP '
                    'group), at kernel virtual addresses: sees what is still in the caches, e.g. after a panic. '
                    'The cores stay halted: resume with ocdrpc.py "targets imx95.a55.0; resume"')
    a = ap.parse_args()
    o = Ocd()
    if a.c is not None:
        for n in range(6):
            o.cmd('catch {imx95.a55.%d arp_examine}' % n)
        print(o.cmd('targets imx95.a55.%d; halt; set s [imx95.a55.%d curstate]' % (a.c, a.c)), file=sys.stderr)
    k = K(o, a.c)
    L = layout()
    banner = k.raw(sym('linux_banner'), 14)
    if banner != b'Linux version ':
        sys.exit('no kernel image at %#x (banner reads %r): wrong KIMG_PHYS, or KASLR on' % (KIMG_PHYS, banner))
    recs = records(k, L)
    if a.g:
        recs = [r for r in recs if re.search(a.g, r[2])]
    for seq, ts, text in recs[-a.n:] if a.n else recs:
        for line in text.split('\n'):
            print('[%5d.%06d] %s' % (ts // 10**9, ts % 10**9 // 1000, line))


if __name__ == '__main__':
    main()
