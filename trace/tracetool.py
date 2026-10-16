#!/usr/bin/env python3
"""tracetool.py: from an OpenCSD snapshot (trace/mksnap.py) to pictures and a time machine.

  tracetool.py index SNAP [--from N] [--count N]   decode (one process per core), rebuild the call
                                                   stacks, save SNAP/index.npz (one row per executed
                                                   range), print each core's span in time
  tracetool.py perfetto SNAP [-o FILE] [--window FROM TO] [--format proto|json]
                                                   per core: the running task (from the traced PID)
                                                   and the nested function slices
                                                   (open in https://ui.perfetto.dev, it stays local)
  tracetool.py speedscope SNAP [-o FILE] [--window FROM TO]   speedscope evented profile
  tracetool.py folded SNAP [-o FILE] [--window FROM TO]       folded stacks (flame graphs)
  tracetool.py nav SNAP [N] [--core C]            step through the recorded history, forward AND back
--window FROM TO: milliseconds from the capture's start (the first traced instruction of any core),
the same axis as the Perfetto timeline and index's span table.

How the stack is rebuilt (control flow is all the ETM gives on the A55: TRCIDR0.TRCDATA = 0):
- each INSTR_RANGE element says how its last instruction left: 'b+link' (BL/BLR) = a call,
  'A64:ret' = a return, else a jump; the next range's address is where it went;
- a call pushes the callee, a return pops, a jump to another function replaces the top (tail call);
- an EXCEPTION pushes a marker and the vector code, EXCEPTION_RET pops back to the marker.
Time: CPU cycle counts (TRCCONFIGR.CCI) if the trace has them, else TIMESTAMP elements
(TRCCONFIGR.TS) interpolated by instruction count; without either, the axis is instructions, shown
as ns at --gips (default 2.5 G instructions/s, measured for dd).
"""
import argparse, array, bisect, glob, json, multiprocessing, os, re, subprocess, sys
import numpy as np

OCSD = '/home/zk47/Project/5.elb1-t113/11.tmp/opencsd/trc_pkt_lister.sh'
KB = '/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work/imx95_19x19_lpddr5_frdm_pro-poky-linux/linux-imx/6.18.20+git/build'
KSRC = '/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work-shared/imx95-19x19-lpddr5-frdm-pro/kernel-source'
X = 'aarch64-linux-gnu-'

RNG = re.compile(r'INSTR_RANGE\(exec range=0x([0-9a-f]+):\[0x([0-9a-f]+)\] num_i\((\d+)\) last_sz\(\d+\) \(ISA=\w+\) ([EN]) (\S+)\s*([^)]*)\)')
TS = re.compile(r'TIMESTAMP\(\s*\[ TS=0x([0-9a-f]+)')
CC = re.compile(r'\[CC=(\d+)\]')
EXC = re.compile(r'OCSD_GEN_TRC_ELEM_EXCEPTION\(')
ERET = re.compile(r'OCSD_GEN_TRC_ELEM_EXCEPTION_RET')
CTX = re.compile(r'PE_CONTEXT\(\(ISA=\w+\) (EL\d)')
VMID = re.compile(r'VMID=0x([0-9a-f]+);')
NACC = re.compile(r'ADDR_NACC\( 0x([0-9a-f]+);')

K_CALL, K_RET, K_JUMP, K_NT = 1, 2, 3, 0
MASK = (1 << 64) - 1
W = 10   # index row: start, end, n_instr, icount_before, kind, depth, func, el, core, pid
# func codes below 0: no symbol, the exception marker (never a row), a core asleep in WFI, kernel code
# with no image (a module), a task switch (a depth-0 row: closes every frame), user code with no image
# (F_USER - pid), a task (F_TASK - pid)
F_UNKNOWN, F_MARK, F_IDLE, F_NOIMG, F_SWITCH, F_USER, F_TASK = -1, -2, -3, -4, -5, -100, -10 ** 9
# instructions per ns that a timed stretch with nothing to count (user code without an image, WFI)
# is NOT credited with: the kernel around it is given IPC 1 at 1.8 GHz, the rest of the time is its own
RATE = 1.8


def s64(v):
    """an address as a signed 64-bit value (kernel addresses don't fit int64 otherwise)"""
    return v - (1 << 64) if v >= 1 << 63 else v


class Syms:
    """function names: System.map for the kernel, nm for each ELF listed in the snapshot"""
    def __init__(self, snap):
        s = []
        for l in open(KB + '/System.map'):
            a, t, n = l.split()[:3]
            if t in 'tTwW':
                s.append((int(a, 16), n))
        self.elfs = {}
        ini = open(sorted(glob.glob(os.path.join(snap, 'cpu_*.ini')))[0]).read()
        for f in sorted(set(re.findall(r'file=(\S+?)_\d+\.bin', ini))):
            if f == 'vmlinux':
                continue
            for cand in ('/home/zk47/Project/5.elb1-t113/11.tmp/imx95-busybox/' + f,):
                if os.path.exists(cand):
                    self.elfs[f] = cand
                    out = subprocess.run([X + 'nm', cand], capture_output=True, text=True).stdout
                    for l in out.splitlines():
                        p = l.split()
                        if len(p) == 3 and p[1] in 'tTwW':
                            s.append((int(p[0], 16), f + ':' + p[2]))
        s.sort()
        self.addr = [a for a, _ in s]
        self.name = [n for _, n in s]

    def idx(self, pc):
        i = bisect.bisect_right(self.addr, pc) - 1
        return i if i >= 0 and pc - self.addr[i] < 0x200000 else -1

    def __call__(self, pc):
        i = self.idx(pc)
        return self.name[i] if i >= 0 else '%#x' % pc


def comms(snap):
    """pid -> process name, from SNAP/pids.txt ('PID NAME' lines, collected on the board while the
    trace ran: /proc/*/comm). A PID reused during the collection gets both names."""
    out = {}
    p = os.path.join(snap, 'pids.txt')
    if os.path.exists(p):
        for l in open(p):
            m = re.match(r'(\d+) (.+)$', l.strip())
            if m:
                pid, n = int(m.group(1)), m.group(2)
                if n not in out.get(pid, '').split('/'):
                    out[pid] = out[pid] + '/' + n if pid in out else n
    return out


def label(f, names, cm):
    if f >= 0:
        return names[f]
    if f == F_IDLE:
        return '[WFI: core asleep]'
    if f == F_NOIMG:
        return '[kernel code without an image]'
    if f == F_SWITCH:
        return '[task switch]'
    if f <= F_TASK:
        return task_name(F_TASK - f, cm)
    if f <= F_USER:
        return '%s [%d] (user code)' % (cm.get(F_USER - f, '?'), F_USER - f)
    return '?'


def task_name(pid, cm):
    return 'idle [0]' if pid == 0 else '%s [%d]' % (cm.get(pid, '?'), pid)


class _Src:
    """the decoder state of one trace source (one core's ETM)"""
    def __init__(self):
        self.stack, self.pending, self.icount, self.el, self.cyc = [], K_JUMP, 0, 0, 0
        self.pid = -1             # CONTEXTIDR_EL2 = the PID (CONFIG_PID_IN_CONTEXTIDR, VHE), traced as VMID
        self.stacks = {}          # pid -> its call stack while it is switched out
        self.credit = None        # the row that gets the untraced time up to the next timestamp
        self.credit_cc = 0
        self.last_ts, self.last_ts_ic = None, 0
        self.nacc = None          # (func, depth) of the stretch without an image being extended


def trace_ids(snap):
    """the trace ID of each ETM in the snapshot (core n = trace ID 0x10 + n)"""
    ids = []
    for f in sorted(glob.glob(os.path.join(snap, 'etm_*.ini'))):
        m = re.search(r'TRCTRACEIDR=0x([0-9A-Fa-f]+)', open(f).read())
        if m:
            ids.append(int(m.group(1), 16))
    return ids or [0x10]


_SYMS = None   # set before the index workers fork; they inherit it


def cmd_index(a):
    """One process per trace ID, each with its own decoder (trc_pkt_lister -id): the sources are
    independent, and a whole six-core buffer indexes in about the time of its busiest core."""
    global _SYMS
    _SYMS = Syms(a.snap)
    jobs = [(a.snap, tid, a.start, a.count) for tid in trace_ids(a.snap)]
    with multiprocessing.get_context('fork').Pool(len(jobs)) as pool:
        res = pool.map(_index_source, jobs)
    # The ETR stops every core at once. A core whose last task was the idle task and that wrote
    # nothing after was asleep in WFI until then (an executing core writes trace): its sleep
    # becomes an idle row up to the capture's last timestamp.
    end_ts = max((int(r['ts'][-1, 2]) for r in res if len(r['ts'])), default=None)
    for r in res:
        rw, ts = r['rows'], r['ts']
        if end_ts is None or not len(rw) or not len(ts) or rw[-1, 9] != 0 or end_ts - ts[-1, 2] < 6144:
            continue
        ic_end = int(rw[-1, 3] + rw[-1, 2])
        n = int((end_ts - ts[-1, 2]) / 6.144 * RATE)
        idle = np.array([[0, 0, n, ic_end, K_JUMP, rw[-1, 5] + 1, F_IDLE, rw[-1, 7], r['core'], 0]], np.int64)
        r['rows'] = np.vstack([rw, idle])
        r['ts'] = np.vstack([ts, [[r['core'], ic_end + n, end_ts]]])
    cat = lambda k, w: np.concatenate([r[k] for r in res]) if res else np.zeros((0, w), np.int64)
    np.savez(os.path.join(a.snap, 'index.npz'), rows=cat('rows', W), ts=cat('ts', 3), cc=cat('cc', 3),
             gaps=cat('gaps', 2), names=np.array(_SYMS.name, dtype=object), start=a.start)
    print('-> ' + os.path.join(a.snap, 'index.npz'))
    coverage(a.snap)


def _index_source(job):
    """The call stack of one trace source, one row per executed range.
    - An overflow (NO_SYNC, TRACE_ON [overflow]) loses trace for a while: the stack starts again.
    - TRACE_ON [begin or filter] is the ETM pausing around WFI: the stack holds, and the sleep becomes
      an idle row.
    - Code with no image (user programs: dynamically linked, ASLR; kernel modules) can't be followed:
      ADDR_NACC. A stretch of it becomes one row, user code named by its process.
    - Time: rows that stand for untraced time (idle, no image) get it as virtual instructions at the
      next timestamp: the time between the two timestamps, less what the counted instructions need
      at RATE. Without that, interpolating by instruction count would stretch the kernel code around
      a sleep or a user-space stretch over its whole duration."""
    snap, tid, start, count = job
    syms, core, s = _SYMS, tid - 0x10, _Src()
    p = subprocess.Popen([OCSD, '-ss_dir', snap, '-decode_only', '-logstdout', '-no_time_print', '-id', '%#x' % tid],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    rows = array.array('q')   # W columns, see W
    tsl = array.array('q')    # core, icount, timestamp
    ccl = array.array('q')    # core, icount, cumulative CPU cycles at each cycle count (TRCCONFIGR.CCI)
    gaps = array.array('q')   # core, icount where trace was lost
    stop_at = start + count if count else None

    def enter(f):
        """the stack once control reached f, given how the previous range left"""
        stack = s.stack
        if s.pending == K_CALL:
            stack.append(f)
        elif s.pending == K_RET:
            if stack and stack[-1] != F_MARK:
                stack.pop()
            if not stack or stack[-1] == F_MARK:
                stack.append(f)          # returned above where the trace began
            elif stack[-1] != f:
                stack[-1] = f            # landed in a different caller than expected
        elif not stack or stack[-1] == F_MARK:
            stack.append(f)
        elif stack[-1] != f:
            stack[-1] = f                # jump into another function: a tail call

    def add(st, en, n, depth, f, credit=False):
        if s.icount >= start and (not stop_at or s.icount < stop_at):
            rows.extend((st, en, n, s.icount, s.pending, depth, f, s.el, core, s.pid))
            if credit and s.credit is None:
                s.credit, s.credit_cc = len(rows) // W - 1, len(ccl)

    for line in p.stdout:
        if 'ID:' not in line:
            continue
        m = RNG.search(line)
        if m:
            st, en, n = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3))
            f = syms.idx(st)
            enter(f)
            taken, extra = m.group(4), m.group(6)
            if taken == 'E' and 'b+link' in extra:
                s.pending = K_CALL
            elif taken == 'E' and 'ret' in extra:
                s.pending = K_RET
            else:
                s.pending = K_JUMP
            add(s64(st), s64(en), n, len(s.stack), f)
            s.icount += n
            s.nacc = None
            if stop_at and s.icount >= stop_at:
                p.kill()                     # this core's window is full
                break
            continue
        m = NACC.search(line)
        if m:
            addr = int(m.group(1), 16)
            f = F_USER - max(s.pid, 0) if addr < 1 << 63 else F_NOIMG
            if s.nacc == (f, len(s.stack)):
                continue                     # the same stretch: nothing to count, the row stands
            enter(f)
            s.pending = K_JUMP
            add(s64(addr), s64(addr), 0, len(s.stack), f, credit=True)
            s.nacc = (f, len(s.stack))
            continue
        if EXC.search(line):
            s.stack.append(F_MARK)           # the exception's frames go above this marker
            s.pending, s.nacc = K_CALL, None
            continue
        if ERET.search(line):
            while s.stack and s.stack[-1] != F_MARK:
                s.stack.pop()
            if s.stack:
                s.stack.pop()
            s.pending, s.nacc = K_JUMP, None
            continue
        if 'NO_SYNC' in line or 'TRACE_ON' in line:
            if 'NO_SYNC' in line or 'overflow' in line:
                if s.icount:
                    gaps.extend((core, s.icount))
                s.stack, s.pending, s.credit = [], K_JUMP, None
            elif s.icount:
                add(0, 0, 0, len(s.stack) + 1, F_IDLE, credit=True)
            s.nacc = None
            continue
        c = CC.search(line)
        if c:
            s.cyc += int(c.group(1))
            ccl.extend((core, s.icount, s.cyc))
        m = TS.search(line)
        if m:
            v = int(m.group(1), 16)
            if s.credit is not None and s.last_ts is not None:
                extra_ = int((v - s.last_ts) / 6.144 * RATE) - (s.icount - s.last_ts_ic)
                if extra_ > 0:
                    k = s.credit
                    rows[k * W + 2] += extra_
                    for j in range(k + 1, len(rows) // W):
                        rows[j * W + 3] += extra_
                    for j in range(s.credit_cc, len(ccl) // 3):
                        ccl[j * 3 + 1] += extra_
                    s.icount += extra_
            s.credit, s.nacc = None, None
            tsl.extend((core, s.icount, v))
            s.last_ts, s.last_ts_ic = v, s.icount
            continue
        if c:
            continue
        m = CTX.search(line)
        if m:
            s.el = int(m.group(1)[2])
            v = VMID.search(line)
            if v and int(v.group(1), 16) != s.pid:
                # a task switch: __switch_to wrote the next PID to CONTEXTIDR_EL2. Both tasks' stacks
                # stop at the same point in __switch_to, so the incoming one continues from its own
                # saved stack (one shared stack piled each task's frames on the last: depth 597)
                pid = int(v.group(1), 16)
                if s.pid >= 0:
                    s.stacks[s.pid] = s.stack
                    s.stack = s.stacks.pop(pid, [])
                s.pid, s.nacc = pid, None
                add(0, 0, 0, 0, F_SWITCH)
                for d, g in enumerate(s.stack):
                    if g != F_MARK:
                        add(0, 0, 0, d + 1, g)   # the incoming task's frames, reopened
    p.wait()
    as_np = lambda v, w: np.frombuffer(v, dtype=np.int64).reshape(-1, w) if len(v) else np.zeros((0, w), np.int64)
    return {'core': core, 'rows': as_np(rows, W), 'ts': as_np(tsl, 3), 'cc': as_np(ccl, 3), 'gaps': as_np(gaps, 2)}


def capture_start(cores):
    """time 0 of every view: the earliest traced instruction of any core"""
    return min(ix.t_ns(int(ix.r[0, 3])) for ix in cores.values() if len(ix.r))


def coverage(snap):
    """Each core's span on the common time axis, and what ran there. The cores of one capture end
    together (the ETR stops them all at once) and, once the ring has wrapped, start together too; a
    core that starts late wrote little (asleep: no trace in WFI)."""
    cores = load(snap, 2.5)
    t0 = capture_start(cores)
    gaps = np.load(os.path.join(snap, 'index.npz'), allow_pickle=True)['gaps']
    gaps = gaps[:, 0] if gaps.shape[1] == 2 else np.zeros(len(gaps), np.int64)
    print('core   from ms    to ms   instructions    ranges  timestamps  cycle counts  gaps  max depth')
    span = {}
    for c, ix in cores.items():
        r = ix.r
        a, b = ix.t_ns(int(r[0, 3])) - t0, ix.t_ns(int(r[-1, 3] + r[-1, 2])) - t0
        span[c] = (a, b, int(r[r[:, 6] >= F_UNKNOWN, 2].sum()))
        print('%4d %9.3f %8.3f %14d %9d %11d %13d %5d %10d' % (c, a / 1e6, b / 1e6, span[c][2], len(r), len(ix.ts),
                                                            len(ix.cc), int((gaps == c).sum()), int(r[:, 5].max())))
    for c in sorted(set(t - 0x10 for t in trace_ids(snap)) - set(cores)):
        print('%4d  no trace in the buffer (asleep, or off)' % c)
    top = max(n for _, _, n in span.values())
    busy = [c for c, (_, _, n) in span.items() if n >= top / 100]
    unit = next(iter(cores.values())).unit or 'instructions at 2.5 GIPS (no timestamps)'
    print('time axis: %s' % unit)
    if len(span) > 1:
        print('every busy core (%s) is in %.3f .. %.3f ms: use that as perfetto --window' % (
            ','.join(map(str, busy)), max(span[c][0] for c in busy) / 1e6, min(span[c][1] for c in busy) / 1e6))
    if any((ix.r[:, 8] >= 0).any() for ix in cores.values()):
        print('time per task (CONTEXTIDR_EL2 = PID; idle [0] includes WFI):')
        for c, ix in cores.items():
            t0_, t1_, pid = ix.tasks()
            if not len(pid):
                continue
            tot = {}
            for p_, d in zip(pid.tolist(), (t1_ - t0_).tolist()):
                tot[p_] = tot.get(p_, 0) + d
            all_ = sum(tot.values()) or 1
            print('  core %d: %s' % (c, ', '.join('%s %.0f%%' % (task_name(p_, ix.comms), 100.0 * d / all_)
                                                for p_, d in sorted(tot.items(), key=lambda x: -x[1])[:5])))

def load(snap, gips):
    """{core: CoreIndex} from SNAP/index.npz (older indexes: no core column, no pid column)"""
    z = np.load(os.path.join(snap, 'index.npz'), allow_pickle=True)
    names = list(z['names'])
    rows = z['rows']
    if rows.shape[1] == 8:
        rows = np.column_stack([rows, np.zeros(len(rows), np.int64)])
    if rows.shape[1] == 9:
        rows = np.column_stack([rows, np.full(len(rows), -1, np.int64)])
    def per_core(key):
        if key not in z.files:
            return np.zeros((0, 3), np.int64)
        v = z[key]
        return np.column_stack([np.zeros(len(v), np.int64), v]) if v.shape[1] == 2 else v
    ts, cc = per_core('ts'), per_core('cc')
    cm = comms(snap)
    out = {}
    for c in sorted(set(int(x) for x in rows[:, 8])):
        out[c] = CoreIndex(c, rows[rows[:, 8] == c][:, [0, 1, 2, 3, 4, 5, 6, 7, 9]], ts[ts[:, 0] == c][:, 1:],
                           cc[cc[:, 0] == c][:, 1:], names, gips, cm)
    return out


class CoreIndex:
    """one core's rows: start, end, n, icount, kind, depth, func, el, pid"""
    def __init__(self, core, rows, ts, cc, names, gips, cm=None):
        self.core, self.r, self.ts, self.cc, self.names, self.gips = core, rows, ts, cc, names, gips
        self.comms = cm or {}
        self.unit = None
        if len(self.ts) >= 2 and self.ts[-1, 1] > self.ts[0, 1]:
            self.unit = 'ETM timestamps (system counter x 256)'
        if len(self.cc) >= 2:
            # cycle-accurate: the CPU clock from cycles against timestamps (1.807 GHz measured), else 1.8
            self.ghz = 1.8
            if self.unit:
                c0 = np.interp(self.ts[0, 0], self.cc[:, 0], self.cc[:, 1])
                c1 = np.interp(self.ts[-1, 0], self.cc[:, 0], self.cc[:, 1])
                self.ghz = (c1 - c0) / ((self.ts[-1, 1] - self.ts[0, 1]) / 6.144)
            self.t0 = (self.ts[0, 1] / 6.144 - np.interp(self.ts[0, 0], self.cc[:, 0], self.cc[:, 1]) / self.ghz) \
                if self.unit else 0.0
            self.unit = 'CPU cycles at %.4f GHz' % self.ghz

    def name(self, f):
        return label(f, self.names, self.comms)

    def t_ns(self, icount):
        return float(self.t_ns_v([icount])[0])

    def t_ns_v(self, icounts):
        """times of instruction indexes, all in one call (np.interp on one value rescans the whole
        table): cycle counts if the trace has them, else timestamps interpolated, else icount/gips"""
        ic = np.asarray(icounts, dtype=np.float64)
        if len(self.cc) >= 2:
            return self.t0 + np.interp(ic, self.cc[:, 0], self.cc[:, 1]) / self.ghz
        if self.unit:
            # i.MX95: the ETM timestamp is the 24 MHz system counter with 8 fractional bits, 6.144 G per
            # second (measured 2026-10-16: 6.137 G against dd's own syscall rate, 0.1 %)
            return np.interp(ic, self.ts[:, 0], self.ts[:, 1]) / 6.144
        return ic / self.gips

    def tasks(self):
        """(t_start, t_end, pid) of each run of one task on this core, from the traced PID (rows
        before the first context packet have none and are left out)"""
        r = self.r
        if not len(r):
            return np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
        pid = r[:, 8]
        cut = np.flatnonzero(np.diff(pid) != 0) + 1
        a = np.concatenate([[0], cut])
        b = np.concatenate([cut, [len(r)]])
        end_ic = np.where(b < len(r), r[np.minimum(b, len(r) - 1), 3], r[-1, 3] + r[-1, 2])
        keep = pid[a] >= 0
        t = self.t_ns_v(np.concatenate([r[a, 3], end_ic]))
        return t[:len(a)][keep], t[len(a):][keep], pid[a][keep]

    def frames(self):
        """(start_icount, end_icount, depth, func) for every function activation in the window.
        A frame stays open while deeper rows (its callees) run; it closes when a row is shallower
        (it returned) or at its own depth with another function (a tail call replaced it). Depth can
        jump by 2 at an exception (the marker level has no rows), so frames are keyed by depth."""
        open_ = []   # [func, start_icount, depth], innermost last
        out = []
        for ic, depth, f in zip(self.r[:, 3].tolist(), self.r[:, 5].tolist(), self.r[:, 6].tolist()):
            while open_ and open_[-1][2] > depth:
                g, s0, d0 = open_.pop()
                out.append((s0, ic, d0, g))
            if open_ and open_[-1][2] == depth and open_[-1][0] != f:
                g, s0, d0 = open_.pop()
                out.append((s0, ic, d0, g))
            if f == F_SWITCH:
                continue                 # depth 0: every frame closed, the next task's rows follow
            if not open_ or open_[-1][2] < depth:
                open_.append([f, ic, depth])
        end = int(self.r[-1, 3] + self.r[-1, 2]) if len(self.r) else 0
        while open_:
            g, s0, d0 = open_.pop()
            out.append((s0, end, d0, g))
        return out

PROC = 'i.MX95 A55 (ETM trace)'


def select(a, cores):
    """the capture's start (time 0 everywhere) and, with --window FROM TO (ms from that start), each
    core's rows cut to that window: all cores side by side over the same interval. A core with
    nothing in the window (idle: no trace in WFI) keeps its track, empty."""
    t_base = capture_start(cores)
    if a.window:
        lo, hi = (t_base + v * 1e6 for v in a.window)
        for ix in cores.values():
            t = ix.t_ns_v(ix.r[:, 3])
            ix.r = ix.r[(t >= lo) & (t < hi)]
        if not any(len(ix.r) for ix in cores.values()):
            sys.exit('nothing traced in %g .. %g ms' % tuple(a.window))
    return cores, t_base


def events(ix, t_base):
    """one core's Begin/End events in call order: arrays (t, begin, func, instructions, icount), t in
    integer ns from the capture's start and never decreasing. Nesting comes from this order, not from
    comparing float times (absolute times near 1.8e9 us rounded a parent's end past its tail-called
    child's start: Perfetto import errors)."""
    fr = np.array(ix.frames(), dtype=np.int64).reshape(-1, 4)
    s0, s1, d, f = fr.T
    nb = len(fr)
    ic = np.concatenate([s0, s1])
    begin = np.concatenate([np.ones(nb, np.int64), np.zeros(nb, np.int64)])
    # at one icount: ends before begins, ends deepest first, begins shallowest first
    o = np.lexsort((np.concatenate([d, -d]), begin, ic))
    t = np.maximum.accumulate(np.maximum(np.round(ix.t_ns_v(ic[o]) - t_base), 0).astype(np.int64))
    return t, begin[o], np.concatenate([f, f])[o], np.concatenate([s1 - s0, np.zeros(nb, np.int64)])[o], ic[o]


def task_events(ix, t_base):
    """the tasks track: one slice per run of a task on the core, in the same tuple form as events()"""
    t0, t1, pid = ix.tasks()
    n = len(pid)
    t = np.empty(2 * n, np.int64)
    t[0::2], t[1::2] = np.round(t0 - t_base), np.round(t1 - t_base)
    t = np.maximum.accumulate(np.maximum(t, 0))
    b = np.tile(np.array([1, 0], np.int64), n)
    f = np.repeat(F_TASK - pid, 2)
    return t, b, f, np.full(2 * n, -1, np.int64), np.zeros(2 * n, np.int64)


def tracks_of(a, cores, t_base):
    """(key, label, sort rank, events) per track: for each core its tasks (when the trace has PIDs)
    above its functions. A traced core with nothing in the buffer keeps an empty, labelled track."""
    out = []
    none = lambda: tuple(np.zeros(0, np.int64) for _ in range(5))
    for c in sorted(set(t - 0x10 for t in trace_ids(a.snap)) - set(cores)):
        out.append((10 + c, 'core %d: no trace in this buffer (asleep in WFI: the ETM is silent)' % c, 2 * c + 1, none()))
    for c, ix in cores.items():
        if (ix.r[:, 8] >= 0).any():
            out.append((20 + c, 'core %d tasks' % c, 2 * c, task_events(ix, t_base)))
        out.append((10 + c, 'core %d (%s)' % (c, ix.unit or 'instructions at %.1f GIPS' % a.gips), 2 * c + 1,
                    events(ix, t_base)))
    return out


def cmd_perfetto(a):
    """Per core: a tasks track (from the traced PID) and the function slices nested in call order,
    times on the common system-counter timestamps. Default format: Perfetto's own protobuf, several
    times smaller than the JSON and quick to load, so a whole multi-core buffer opens in the UI;
    --format json for other viewers."""
    cores, t_base = select(a, load(a.snap, a.gips))
    tracks = tracks_of(a, cores, t_base)
    name = next(iter(cores.values())).name
    out = a.o or os.path.join(a.snap, 'trace.perfetto-trace' if a.format == 'proto' else 'trace.perfetto.json')
    (_write_proto if a.format == 'proto' else _write_json)(out, tracks, name)
    print('%d slices on %d track(s)%s -> %s (%.1f MB; open in https://ui.perfetto.dev, processed in the browser)' % (
        sum(int(ev[1].sum()) for *_, ev in tracks), len(tracks),
        ' in %g .. %g ms' % tuple(a.window) if a.window else '', out, os.path.getsize(out) / 1e6))


def _write_json(out, tracks, name):
    # tid = 10 + core (functions), 20 + core (tasks): Perfetto maps tid 0 to the process's main thread
    # (tid == pid), which merged core 0 into core 1's track (misplaced_end_event) when the pid was 1
    qn = {}
    with open(out, 'w') as fo:
        fo.write('{"displayTimeUnit": "ns", "traceEvents": [\n')
        fo.write(json.dumps({'name': 'process_name', 'ph': 'M', 'pid': 1, 'args': {'name': PROC}}))
        for tid, lbl, rank, (t, b, f, n, ic) in tracks:
            fo.write(',\n' + json.dumps({'name': 'thread_name', 'ph': 'M', 'pid': 1, 'tid': tid, 'args': {'name': lbl}}))
            fo.write(',\n' + json.dumps({'name': 'thread_sort_index', 'ph': 'M', 'pid': 1, 'tid': tid,
                                         'args': {'sort_index': rank}}))
            for t_, b_, f_, n_, ic_ in zip(t.tolist(), b.tolist(), f.tolist(), n.tolist(), ic.tolist()):
                if b_:
                    nm = qn.get(f_) or qn.setdefault(f_, json.dumps(name(f_)))
                    args = ', "args": {"instructions": %d, "first_instruction": %d}' % (n_, ic_) if n_ >= 0 else ''
                    fo.write(',\n{"name": %s, "ph": "B", "ts": %.3f, "pid": 1, "tid": %d%s}' % (nm, t_ / 1000.0, tid, args))
                else:
                    fo.write(',\n{"ph": "E", "ts": %.3f, "pid": 1, "tid": %d}' % (t_ / 1000.0, tid))
        fo.write('\n]}\n')


def _varint(v):
    b = bytearray()
    while v > 0x7f:
        b.append(v & 0x7f | 0x80)
        v >>= 7
    b.append(v)
    return bytes(b)


def _fld(num, v):
    """one protobuf field: an int (>= 0) as a varint, str/bytes length-delimited"""
    if isinstance(v, int):
        return _varint(num << 3) + _varint(v)
    if isinstance(v, str):
        v = v.encode()
    return _varint(num << 3 | 2) + _varint(len(v)) + v


def _write_proto(out, tracks, name):
    """Perfetto's trace format, by hand (field numbers from the perfetto package's descriptors):
    Trace {packet 1}; TracePacket {timestamp 8, trusted_packet_sequence_id 10, track_event 11,
    interned_data 12, sequence_flags 13 (1 state cleared, 2 needs state), track_descriptor 60};
    TrackDescriptor {uuid 1, name 2, parent_uuid 5, child_ordering 11 (3 explicit), sibling_order_rank 12};
    TrackEvent {debug_annotations 4, type 9 (1 begin, 2 end), name_iid 10, track_uuid 11};
    DebugAnnotation {name_iid 1, uint_value 3}; InternedData {event_names 2, debug_annotation_names 3}."""
    root = 1000
    used = sorted(set(int(x) for *_, ev in tracks for x in np.unique(ev[2])))
    iid = {f: i + 1 for i, f in enumerate(used)}
    seq = _fld(10, 1)
    interned = _fld(12, b''.join(_fld(2, _fld(1, i) + _fld(2, name(f))) for f, i in iid.items())
                    + _fld(3, _fld(1, 1) + _fld(2, 'instructions')) + _fld(3, _fld(1, 2) + _fld(2, 'first_instruction')))
    # all tracks' events in one time-ordered stream (a stable sort keeps each track's call order)
    T = np.concatenate([ev[0] for *_, ev in tracks])
    U = np.concatenate([np.full(len(ev[0]), root + key, np.int64) for key, _, _, ev in tracks])
    B, F, N, IC = (np.concatenate([ev[k] for *_, ev in tracks]) for k in (1, 2, 3, 4))
    o = np.argsort(T, kind='stable')
    with open(out, 'wb') as fo:
        fo.write(_fld(1, seq + _fld(60, _fld(1, root) + _fld(2, PROC) + _fld(11, 3))))
        for key, lbl, rank, _ in tracks:
            fo.write(_fld(1, seq + _fld(60, _fld(1, root + key) + _fld(5, root) + _fld(2, lbl) + _fld(12, rank))))
        flags = _fld(13, 3) + interned           # the first event packet carries the names
        for t, u, b, f, n, ic in zip(T[o].tolist(), U[o].tolist(), B[o].tolist(), F[o].tolist(), N[o].tolist(),
                                     IC[o].tolist()):
            if b:
                te = b'\x48\x01' + _fld(11, u) + _fld(10, iid[f])
                if n >= 0:
                    te += _fld(4, b'\x08\x01' + _fld(3, n)) + _fld(4, b'\x08\x02' + _fld(3, ic))
            else:
                te = b'\x48\x02' + _fld(11, u)
            fo.write(_fld(1, _fld(8, t) + seq + _fld(11, te) + flags))
            flags = b'\x68\x02'


def cmd_speedscope(a):
    cores, t_base = select(a, load(a.snap, a.gips))
    evs = {c: events(ix, t_base) for c, ix in cores.items()}
    used = sorted(set(int(x) for ev in evs.values() for x in np.unique(ev[2])))
    fid = {f: i for i, f in enumerate(used)}
    any_ix = next(iter(cores.values()))
    profiles = []
    for c, (t, b, f, n, ic) in evs.items():
        ev = [{'type': 'O' if b_ else 'C', 'frame': fid[f_], 'at': t_} for t_, b_, f_ in zip(t.tolist(), b.tolist(), f.tolist())]
        profiles.append({'type': 'evented', 'name': 'core %d' % c, 'unit': 'nanoseconds',
                         'startValue': ev[0]['at'] if ev else 0, 'endValue': ev[-1]['at'] if ev else 0, 'events': ev})
    prof = {'$schema': 'https://www.speedscope.app/file-format-schema.json',
            'shared': {'frames': [{'name': any_ix.name(f)} for f in used]},
            'profiles': profiles, 'name': os.path.basename(a.snap.rstrip('/'))}
    out = a.o or os.path.join(a.snap, 'trace.speedscope.json')
    json.dump(prof, open(out, 'w'))
    print('%d profile(s) -> %s (open in https://www.speedscope.app, local)' % (len(profiles), out))

def cmd_folded(a):
    """Stacks rooted at the core (several cores) and the task (when the trace has PIDs). Rows without
    instructions to count (user code without an image, WFI) weigh their virtual instructions: their
    time at RATE per ns."""
    cores, _ = select(a, load(a.snap, a.gips))
    agg = {}
    kinds = set()
    for c, ix in cores.items():
        kinds.add(_fold(ix, agg, a, 'core %d' % c if len(cores) > 1 else None))
    ix0 = next(iter(cores.values()))
    out = a.o or os.path.join(a.snap, 'trace.folded')
    with open(out, 'w') as fo:
        for (prefix, pid, stack), v in sorted(agg.items(), key=lambda x: -x[1]):
            fo.write('%s %d\n' % (';'.join(([prefix] if prefix else []) + ([task_name(pid, ix0.comms)] if pid >= 0 else [])
                                           + [ix0.name(g) for g in stack]), round(v)))
    print('%d stacks over %d core(s), weighted by %s -> %s (flamegraph.pl, or speedscope)' % (
        len(agg), len(cores), '/'.join(sorted(kinds)), out))


def _fold(ix, agg, a, prefix):
    stack = []
    cyc = len(ix.cc) >= 2 and not a.instructions   # weight by CPU cycles when the trace has them
    if cyc:
        ic_, n_ = ix.r[:, 3].astype(np.float64), ix.r[:, 2].astype(np.float64)
        weights = np.interp(ic_ + n_, ix.cc[:, 0], ix.cc[:, 1]) - np.interp(ic_, ix.cc[:, 0], ix.cc[:, 1])
    else:
        weights = ix.r[:, 2]
    for depth, f, pid, w in zip(ix.r[:, 5].tolist(), ix.r[:, 6].tolist(), ix.r[:, 8].tolist(), weights.tolist()):
        if f == F_SWITCH:
            continue
        del stack[depth:]
        while len(stack) < depth:
            stack.append(f)
        stack[depth - 1] = f
        k = (prefix, pid, tuple(stack))     # names joined once per distinct stack, at the end
        agg[k] = agg.get(k, 0) + w
    return 'CPU cycles' if cyc else 'instructions'


def cmd_nav(a):
    import capstone
    cores = load(a.snap, a.gips)
    core = a.core if a.core is not None else min(cores)
    if core not in cores:
        sys.exit('no core %d in this trace; it has %s' % (core, sorted(cores)))
    ix = cores[core]
    print('core %d (of %s)' % (core, sorted(cores)))
    r = ix.r
    if not len(r):
        sys.exit('empty index')
    base = int(r[0, 3])
    cum = r[:, 3]                       # icount at the start of each range
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    images = []                         # (va, bytes)
    ini = open(os.path.join(a.snap, 'cpu_%d.ini' % core)).read()
    for f, va, ln in re.findall(r'file=(\S+)\naddress=0x([0-9A-F]+)\nlength=0x([0-9A-F]+)', ini):
        images.append((int(va, 16), open(os.path.join(a.snap, f), 'rb').read()))
    a2l = {}

    def code(pc):
        for va, b in images:
            if va <= pc < va + len(b) - 4:
                return b[pc - va:pc - va + 4]
        return None

    def src(pc):
        if pc not in a2l:
            elf = KB + '/vmlinux' if pc >= 0xffff000000000000 else None
            if elf is None:
                a2l[pc] = ''
            else:
                o = subprocess.run([X + 'addr2line', '-e', elf, '%#x' % pc], capture_output=True, text=True).stdout.strip()
                a2l[pc] = o.replace('/usr/src/kernel/', '')
        return a2l[pc]

    def locate(i):
        k = int(np.searchsorted(cum, i, side='right') - 1)
        return k, (int(r[k, 0]) & MASK) + int(i - cum[k]) * 4

    def show(i):
        k, pc = locate(i)
        st, en, n, ic, kind, depth, f, el = (int(x) for x in r[k, :8])
        if f < F_UNKNOWN:      # untraced time: user code without an image, WFI
            print('#%-11d t=%.3f us  EL%d  %s   (no instructions recorded here)' % (
                i, (ix.t_ns(i) - t_base) / 1000.0, el, ix.name(f)))
            return
        b = code(pc)
        ins = next(md.disasm(b, pc), None) if b else None
        line = src(pc)
        text = ''
        if line and ':' in line:
            fn, _, no = line.rpartition(':')
            try:
                text = open(os.path.join(KSRC, fn), errors='replace').read().split('\n')[int(no) - 1].strip()
            except (OSError, ValueError, IndexError):
                pass
        print('#%-11d t=%.3f us  EL%d  %s+%#x  %#x: %s' % (i, (ix.t_ns(i) - t_base) / 1000.0, el, ix.name(f),
                                                            pc - _fstart(f, pc), pc, _fmt(ins)))
        if line:
            print('             %s   %s' % (line, text))

    def _fstart(f, pc):
        return syms_addr[f] if f >= 0 else pc

    def _fmt(ins):
        return '%s %s' % (ins.mnemonic, ins.op_str) if ins else '(no image)'

    z = np.load(os.path.join(a.snap, 'index.npz'), allow_pickle=True)
    syms = Syms(a.snap)
    syms_addr = syms.addr

    def backtrace(i):
        """each live frame and the instruction where its activation began, walking back from i.
        Deeper rows are callees, the first shallower row is the caller. At the same depth, a run of
        the same function extends the activation; a different function there is what this frame
        replaced by a tail call (not a frame of its own), so it only ends the extension."""
        k, _ = locate(i)
        level, func, entry, extending = int(r[k, 5]), int(r[k, 6]), k, True
        out = []
        j = k - 1
        while j >= 0 and level > 0:
            d = int(r[j, 5])
            if d > level:
                pass
            elif d == level:
                if extending and int(r[j, 6]) == func:
                    entry = j
                else:
                    extending = False
            else:
                out.append((func, entry))
                level, func, entry, extending = d, int(r[j, 6]), j, True
            j -= 1
        if func != F_SWITCH:            # the depth-0 row a task switch leaves is no caller
            out.append((func, entry))
        for n, (f, e) in enumerate(out):
            print('  #%-2d %-40s entered at instruction #%d' % (n, ix.name(f), int(cum[e])))

    t_base = ix.t_ns(base)               # times are shown from the start of the recording
    total = int(r[-1, 3] + r[-1, 2])
    cur = max(base, min(int(a.n) if a.n is not None else base, total - 1))
    print('recorded: instructions #%d .. #%d; commands: s/b [N] step fwd/back, n/p next/prev range, '
          'f/F next/prev call or return, g N goto, find NAME / rfind NAME, bt, q' % (base, total - 1))
    show(cur)
    while True:
        try:
            c = input('(trace) ').strip().split()
        except EOFError:
            break
        if not c:
            continue
        op, arg = c[0], c[1] if len(c) > 1 else None
        if op == 'q':
            break
        if op in ('h', 'help', '?'):
            print('  s [N] / b [N]   step forward / back N instructions (default 1)\n'
                  '  n / p           next / previous executed range (a straight-line block)\n'
                  '  f / F           forward to the next call or return / back to before this call\n'
                  '  find NAME       forward to the next entry into a function (substring match)\n'
                  '  rfind NAME      back to the previous entry into a function\n'
                  '  g N             go to instruction #N\n'
                  '  bt              call stack here, with the instruction where each frame began\n'
                  '  q               quit')
            continue
        if op in ('s', 'b'):
            cur += (1 if op == 's' else -1) * int(arg or 1)
        elif op in ('n', 'p'):
            k, _ = locate(cur)
            k = min(max(k + (1 if op == 'n' else -1), 0), len(r) - 1)
            cur = int(cum[k])
        elif op in ('f', 'F'):
            k, _ = locate(cur)
            step = 1 if op == 'f' else -1
            d0 = r[k, 5]
            k += step
            while 0 <= k < len(r) and r[k, 5] == d0:
                k += step
            k = min(max(k, 0), len(r) - 1)
            cur = int(cum[k])
        elif op == 'g':
            cur = int(arg)
        elif op in ('find', 'rfind') and arg:
            k, _ = locate(cur)
            rng = range(k + 1, len(r)) if op == 'find' else range(k - 1, -1, -1)
            # an entry: the previous range ended in a call (or an exception took us here)
            hit = next((j for j in rng if arg in ix.name(int(r[j, 6])) and j > 0 and r[j, 5] > r[j - 1, 5]), None)
            if hit is None:
                print('not found')
                continue
            cur = int(cum[hit])
        elif op == 'bt':
            backtrace(cur)
            continue
        cur = max(base, min(cur, total - 1))
        show(cur)


EXAMPLES = '''
examples (from trace/; ss-t5-cc has timestamps + cycle counts, the most accurate):
  ./tracetool.py nav ss-t5-cc                  step through the history (type h for the commands)
  ./tracetool.py perfetto ss-t5-cc             then open ss-t5-cc/trace.perfetto-trace in ui.perfetto.dev
  ./tracetool.py index ss-t7-sinkfirst         six cores, all 82 M instructions (about a minute)
  ./tracetool.py perfetto ss-t7-sinkfirst      the whole buffer, one track per core
  ./tracetool.py perfetto ss-t7-sinkfirst --window 3 4    one millisecond of all six cores
  ./tracetool.py nav ss-t7-sinkfirst --core 3  step through core 3
  ./tracetool.py speedscope ss-t5-cc           then open ss-t5-cc/trace.speedscope.json in speedscope.app
  xdg-open ss-t5-cc/flamegraph.svg             the flame graph (any browser)
  ./tracetool.py index ss-t3-start --count 3000000    a long capture: index the first 3 M instructions
'''


def main():
    if len(sys.argv) == 1:
        print(__doc__ + EXAMPLES)
        sys.exit(0)
    ap = argparse.ArgumentParser(epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('index'); p.add_argument('snap'); p.add_argument('--from', dest='start', type=int, default=0)
    p.add_argument('--count', type=int, default=0, help='instructions to keep per core (0 = all)')
    for c in ('perfetto', 'speedscope', 'folded'):
        p = sp.add_parser(c); p.add_argument('snap'); p.add_argument('-o'); p.add_argument('--gips', type=float, default=2.5)
        p.add_argument('--window', nargs=2, type=float, metavar=('FROM', 'TO'),
                       help='only this interval, in ms from the capture start (as in the index span table)')
        if c == 'perfetto':
            p.add_argument('--format', choices=('proto', 'json'), default='proto',
                           help='proto: Perfetto\'s own format, compact (default); json: Chrome trace events')
        if c == 'folded':
            p.add_argument('--instructions', action='store_true', help='weight by instructions even with cycle counts')
    p = sp.add_parser('nav'); p.add_argument('snap'); p.add_argument('n', nargs='?'); p.add_argument('--gips', type=float, default=2.5)
    p.add_argument('--core', type=int, help='which core to step through (default: the lowest traced)')
    a = ap.parse_args()
    {'index': cmd_index, 'perfetto': cmd_perfetto, 'speedscope': cmd_speedscope, 'folded': cmd_folded,
     'nav': cmd_nav}[a.cmd](a)


if __name__ == '__main__':
    main()
