#!/usr/bin/env python3
"""jtagtop.py SAMPLES [--pids FILE | --tty /dev/ttyACM2] [--elf ELF] [--top N]

A whole-system profile from PC samples taken through JTAG (ocd.sh top; imx95-jtag.tcl
a55_top_samples). Nothing runs on the board and no core is halted: each sample is an A55's own PC
sample register (PMPCSR) with CONTEXTIDR_EL2 of the same instant, which this kernel keeps as the
running task's PID (CONFIG_PID_IN_CONTEXTIDR, VHE). Prints per core the busy share and its tasks,
then the busiest tasks and functions over all cores.
  --pids FILE   'PID NAME' lines (board/scenario-real.sh's /tmp/pidmap.txt)
  --tty PORT    ask the board's console for /proc/PID/comm of the PIDs seen, after sampling
  --elf ELF     symbols for user code (a static program, e.g. the kit's busybox_unstripped)
"""
import argparse, bisect, collections, re, subprocess, sys

KB = '/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work/imx95_19x19_lpddr5_frdm_pro-poky-linux/linux-imx/6.18.20+git/build'
BOARDQ = '/home/zk47/Learning/yocto-imx95-pro/sources/meta-fastboot-imx95/scripts/boardq.py'


def symbols(elf=None):
    s = []
    if elf is None:
        for l in open(KB + '/System.map'):
            a, t, n = l.split()[:3]
            if t in 'tTwW':
                s.append((int(a, 16), n))
    else:
        out = subprocess.run(['aarch64-linux-gnu-nm', elf], capture_output=True, text=True).stdout
        for l in out.splitlines():
            p = l.split()
            if len(p) == 3 and p[1] in 'tTwW':
                s.append((int(p[0], 16), p[2]))
    s.sort()
    return [a for a, _ in s], [n for _, n in s]


def lookup(tab, pc):
    addr, name = tab
    i = bisect.bisect_right(addr, pc) - 1
    return name[i] if i >= 0 and pc - addr[i] < 0x100000 else None


def board_names(tty, pids):
    """/proc/PID/comm of the PIDs still alive; 40 PIDs a command (a 1.4 KB command line came back
    empty through the console)"""
    cmds = ['for p in %s; do [ -r /proc/$p/comm ] && echo "@@ $p $(cat /proc/$p/comm)"; done' % ' '.join(map(str, pids[i:i + 40]))
            for i in range(0, len(pids), 40)]
    out = subprocess.run(['python3', BOARDQ, '--port', tty] + cmds, capture_output=True, text=True, timeout=120).stdout
    return {int(m.group(1)): m.group(2) for m in re.finditer(r'^@@ (\d+) (.+)$', out, re.M)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('samples')
    ap.add_argument('--pids')
    ap.add_argument('--tty')
    ap.add_argument('--elf')
    ap.add_argument('--top', type=int, default=12)
    a = ap.parse_args()

    rows, none = [], 0
    for l in open(a.samples):
        p = l.split()
        if len(p) != 4:
            continue
        c, lo, hi, pid = int(p[0]), int(p[1], 0), int(p[2], 0), int(p[3], 0)
        if lo == 0xffffffff:
            none += 1               # no sample (the core was halted or powered down)
            continue
        pc = ((hi & 0xffffff) << 32) | lo
        if pc & (1 << 55):
            pc |= 0xff << 56        # bits 55:0, bit 55 extended: kernel addresses start 0xffff
        rows.append((c, pc, (hi >> 29) & 3, pid))
    if not rows:
        sys.exit('no samples')

    names = {}
    if a.pids:
        for l in open(a.pids):
            m = re.match(r'(\d+) (.+)$', l.strip())
            if m:
                names[int(m.group(1))] = m.group(2)
    if a.tty:
        names.update(board_names(a.tty, sorted(set(r[3] for r in rows) - {0})))
    task = lambda pid: 'idle' if pid == 0 else '%s [%d]' % (names.get(pid, '?'), pid)
    ksyms, usyms = symbols(), symbols(a.elf) if a.elf else None

    def func(pc, el, pid):
        if el == 0:
            n = lookup(usyms, pc) if usyms else None
            return '%s (user)' % (n or names.get(pid, '?'))
        return lookup(ksyms, pc) or '%#x' % pc

    per_core = collections.defaultdict(list)
    for r in rows:
        per_core[r[0]].append(r)
    print('%d samples (%d without a PC), %s per core' % (len(rows), none,
          '/'.join(str(len(per_core[c])) for c in sorted(per_core))))
    print('core  busy   tasks (share of all samples on that core)')
    for c in sorted(per_core):
        rc = per_core[c]
        busy = sum(1 for r in rc if r[3] != 0)
        t = collections.Counter(task(r[3]) for r in rc if r[3] != 0)
        print('%4d %5.1f %%  %s' % (c, 100.0 * busy / len(rc), ', '.join(
            '%s %.1f %%' % (k, 100.0 * v / len(rc)) for k, v in t.most_common(5)) or '-'))
    busy = [r for r in rows if r[3] != 0]
    print('\nall cores: %.1f %% busy; the busiest tasks (share of all samples):' % (100.0 * len(busy) / len(rows)))
    for k, v in collections.Counter(task(r[3]) for r in busy).most_common(a.top):
        print('  %5.1f %%  %s' % (100.0 * v / len(rows), k))
    print('the busiest functions, idle left out (kernel at EL2, user code at EL0):')
    fc = collections.Counter((func(r[1], r[2], r[3]), r[2]) for r in busy)
    for (k, el), v in fc.most_common(a.top):
        print('  %5.1f %%  EL%d  %s' % (100.0 * v / len(rows), el, k))


if __name__ == '__main__':
    main()
