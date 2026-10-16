#!/usr/bin/env python3
"""tracetool.py: from an OpenCSD snapshot (trace/mksnap.py) to pictures and a time machine.

  tracetool.py index SNAP [--from N] [--count N]   decode once, rebuild the call stack, save
                                                   SNAP/index.npz (one row per executed range)
  tracetool.py perfetto SNAP [-o FILE]            Chrome/Perfetto JSON: nested function slices
                                                   (open in https://ui.perfetto.dev, it stays local)
  tracetool.py speedscope SNAP [-o FILE]          speedscope evented profile (https://www.speedscope.app)
  tracetool.py folded SNAP [-o FILE]              folded stacks, instruction-weighted (flame graphs)
  tracetool.py nav SNAP [N]                       step through the recorded history, forward AND back

How the stack is rebuilt (control flow is all the ETM gives on the A55: TRCIDR0.TRCDATA = 0):
- each INSTR_RANGE element says how its last instruction left: 'b+link' (BL/BLR) = a call,
  'A64:ret' = a return, else a jump; the next range's address is where it went;
- a call pushes the callee, a return pops, a jump to another function replaces the top (tail call);
- an EXCEPTION pushes a marker and the vector code, EXCEPTION_RET pops back to the marker.
Time: TIMESTAMP elements (TRCCONFIGR.TS) are interpolated by instruction count; without them
the axis is instructions, shown as ns at --gips (default 2.5 G instructions/s, measured for dd).
"""
import argparse, bisect, json, os, re, subprocess, sys
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

K_CALL, K_RET, K_JUMP, K_NT = 1, 2, 3, 0
MASK = (1 << 64) - 1


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
        ini = open(sorted(__import__('glob').glob(os.path.join(snap, 'cpu_*.ini')))[0]).read()
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


def decode(snap):
    p = subprocess.Popen([OCSD, '-ss_dir', snap, '-decode_only', '-logstdout', '-no_time_print'],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    return p


class _Src:
    """the decoder state of one trace source (one core's ETM)"""
    def __init__(self):
        self.stack, self.pending, self.icount, self.el, self.cyc = [], K_JUMP, 0, 0, 0


IDRE = re.compile(r'ID:([0-9a-f]+);')


def cmd_index(a):
    """Decode once; one call-stack machine per trace ID (core n = trace ID 0x10 + n). An overflow
    (NO_SYNC/TRACE_ON [overflow]) loses that core's trace for a while: its stack starts again."""
    syms = Syms(a.snap)
    p = decode(a.snap)
    rows = []          # start, end, n_instr, icount_before, kind, depth, func_idx, el, core
    tsl = []           # (core, icount, timestamp)
    ccl = []           # (core, icount, cumulative CPU cycles) at each cycle count (TRCCONFIGR.CCI)
    gaps = []          # (core, icount) where trace was lost
    srcs = {}
    stop_at = a.start + a.count if a.count else None
    for line in p.stdout:
        idm = IDRE.search(line)
        if not idm:
            continue
        core = int(idm.group(1), 16) - 0x10
        s = srcs.get(core)
        if s is None:
            s = srcs[core] = _Src()
        m = RNG.search(line)
        if m:
            st, en, n = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3))
            f = syms.idx(st)
            stack = s.stack
            if s.pending == K_CALL:
                stack.append(f)
            elif s.pending == K_RET:
                if stack and stack[-1] != -2:
                    stack.pop()
                if not stack or stack[-1] == -2:
                    stack.append(f)          # returned above where the trace began
                elif stack[-1] != f:
                    stack[-1] = f            # landed in a different caller than expected
            elif not stack or stack[-1] == -2:
                stack.append(f)
            elif stack[-1] != f:
                stack[-1] = f                # jump into another function: a tail call
            taken, extra = m.group(4), m.group(6)
            if taken == 'E' and 'b+link' in extra:
                s.pending = K_CALL
            elif taken == 'E' and 'ret' in extra:
                s.pending = K_RET
            else:
                s.pending = K_JUMP
            if s.icount >= a.start and (not stop_at or s.icount < stop_at):
                rows.append((s64(st), s64(en), n, s.icount, s.pending, len(stack), f, s.el, core))
            s.icount += n
            if stop_at and len(srcs) == 1 and s.icount >= stop_at:
                p.kill()                     # one core: stop decoding once the window is full
                break
            continue
        if EXC.search(line):
            s.stack.append(-2)               # the exception's frames go above this marker
            s.pending = K_CALL
            continue
        if ERET.search(line):
            while s.stack and s.stack[-1] != -2:
                s.stack.pop()
            if s.stack:
                s.stack.pop()
            s.pending = K_JUMP
            continue
        if 'NO_SYNC' in line or 'TRACE_ON' in line:
            if s.icount:
                gaps.append((core, s.icount))
            s.stack, s.pending = [], K_JUMP
            continue
        c = CC.search(line)
        if c:
            s.cyc += int(c.group(1))
            ccl.append((core, s.icount, s.cyc))
        m = TS.search(line)
        if m:
            tsl.append((core, s.icount, int(m.group(1), 16)))
            continue
        if c:
            continue
        m = CTX.search(line)
        if m:
            s.el = int(m.group(1)[2])
    p.wait()
    r = np.array(rows, dtype=np.int64).reshape(-1, 9) if rows else np.zeros((0, 9), np.int64)
    np.savez(os.path.join(a.snap, 'index.npz'), rows=r, ts=np.array(tsl, dtype=np.int64).reshape(-1, 3),
             cc=np.array(ccl, dtype=np.int64).reshape(-1, 3), gaps=np.array(gaps, dtype=np.int64).reshape(-1, 2),
             names=np.array(syms.name, dtype=object), start=a.start)
    for core in sorted(srcs):
        rc = r[r[:, 8] == core]
        print('core %d: %d ranges, %d instructions, %d timestamps, %d cycle counts, %d gaps, max depth %d' % (
            core, len(rc), int(rc[:, 2].sum()) if len(rc) else 0, sum(1 for t in tsl if t[0] == core),
            sum(1 for t in ccl if t[0] == core), sum(1 for g in gaps if g[0] == core),
            int(rc[:, 5].max()) if len(rc) else 0))
    print('-> ' + os.path.join(a.snap, 'index.npz'))

def load(snap, gips):
    """{core: CoreIndex} from SNAP/index.npz (older single-core indexes have no core column)"""
    z = np.load(os.path.join(snap, 'index.npz'), allow_pickle=True)
    names = list(z['names'])
    rows = z['rows']
    if rows.shape[1] == 8:
        rows = np.column_stack([rows, np.zeros(len(rows), np.int64)])
    def per_core(key):
        if key not in z.files:
            return np.zeros((0, 3), np.int64)
        v = z[key]
        return np.column_stack([np.zeros(len(v), np.int64), v]) if v.shape[1] == 2 else v
    ts, cc = per_core('ts'), per_core('cc')
    out = {}
    for c in sorted(set(int(x) for x in rows[:, 8])):
        out[c] = CoreIndex(c, rows[rows[:, 8] == c][:, :8], ts[ts[:, 0] == c][:, 1:], cc[cc[:, 0] == c][:, 1:],
                           names, gips)
    return out


class CoreIndex:
    def __init__(self, core, rows, ts, cc, names, gips):
        self.core, self.r, self.ts, self.cc, self.names, self.gips = core, rows, ts, cc, names, gips
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
        return self.names[f] if f >= 0 else '?'

    def t_ns(self, icount):
        """time of an instruction index: timestamps interpolated if there are any, else icount/gips"""
        if len(self.cc) >= 2:
            return self.t0 + float(np.interp(icount, self.cc[:, 0], self.cc[:, 1])) / self.ghz
        if self.unit:
            ic, ts = self.ts[:, 0], self.ts[:, 1]
            # i.MX95: the ETM timestamp is the 24 MHz system counter with 8 fractional bits, 6.144 G per
            # second (measured 2026-10-16: 6.137 G against dd's own syscall rate, 0.1 %)
            return float(np.interp(icount, ic, ts)) / 6.144
        return icount / self.gips

    def frames(self):
        """(start_icount, end_icount, depth, func) for every function activation in the window.
        A frame stays open while deeper rows (its callees) run; it closes when a row is shallower
        (it returned) or at its own depth with another function (a tail call replaced it). Depth can
        jump by 2 at an exception (the marker level has no rows), so frames are keyed by depth."""
        open_ = []   # [func, start_icount, depth], innermost last
        out = []
        for st, en, n, ic, kind, depth, f, el in self.r:
            ic, depth, f = int(ic), int(depth), int(f)
            while open_ and open_[-1][2] > depth:
                g, s0, d0 = open_.pop()
                out.append((s0, ic, d0, g))
            if open_ and open_[-1][2] == depth and open_[-1][0] != f:
                g, s0, d0 = open_.pop()
                out.append((s0, ic, d0, g))
            if not open_ or open_[-1][2] < depth:
                open_.append([f, ic, depth])
        end = int(self.r[-1, 3] + self.r[-1, 2]) if len(self.r) else 0
        while open_:
            g, s0, d0 = open_.pop()
            out.append((s0, end, d0, g))
        return out

def cmd_perfetto(a):
    """Begin/End events in call order, one track (tid) per core. Nesting comes from the order, not
    from comparing float times (absolute times near 1.8e9 us rounded a parent's end past its
    tail-called child's start: Perfetto import errors). Times: integer ns from the earliest
    instruction of any core, so the cores line up on the common system-counter timestamps."""
    cores = load(a.snap, a.gips)
    t_base = min(ix.t_ns(int(ix.r[0, 3])) for ix in cores.values() if len(ix.r))
    out_ev = [{'name': 'process_name', 'ph': 'M', 'pid': 1, 'args': {'name': 'i.MX95 A55 (ETM trace)'}}]
    nfr = 0
    # tid = 10 + core: Perfetto maps tid 0 to the process's main thread (tid == pid), which merged
    # core 0 into core 1's track (misplaced_end_event) when the pid was 1
    for c, ix in cores.items():
        tid = 10 + c
        fr = ix.frames()
        nfr += len(fr)
        out_ev.append({'name': 'thread_name', 'ph': 'M', 'pid': 1, 'tid': tid,
                       'args': {'name': 'core %d (%s)' % (c, ix.unit or 'instructions at %.1f GIPS' % a.gips)}})
        out_ev.append({'name': 'thread_sort_index', 'ph': 'M', 'pid': 1, 'tid': tid, 'args': {'sort_index': c}})
        ev = []
        for s0, s1, d, f in fr:
            ev.append((s0, 1, d, f, s1 - s0))   # B: at equal icount after closes, shallow first
            ev.append((s1, 0, -d, f, 0))        # E: at equal icount before opens, deep first
        ev.sort(key=lambda x: (x[0], x[1], x[2]))
        last = None
        for ic, kind, d, f, n in ev:
            t = int(round(ix.t_ns(ic) - t_base))
            t = t if last is None else max(last, t)     # integer ns, never decreasing per track
            last = t
            e = {'name': ix.name(f), 'ph': 'B' if kind else 'E', 'ts': t / 1000.0, 'pid': 1, 'tid': tid}
            if kind:
                e['args'] = {'instructions': int(n), 'first_instruction': int(ic)}
            out_ev.append(e)
    out = a.o or os.path.join(a.snap, 'trace.perfetto.json')
    json.dump({'traceEvents': out_ev, 'displayTimeUnit': 'ns'}, open(out, 'w'))
    print('%d slices on %d core track(s) -> %s (open in https://ui.perfetto.dev: processed in the browser)' % (
        nfr, len(cores), out))

def cmd_speedscope(a):
    cores = load(a.snap, a.gips)
    frames_by_core = {c: ix.frames() for c, ix in cores.items()}
    names = sorted(set(f for fr in frames_by_core.values() for _, _, _, f in fr))
    fid = {f: i for i, f in enumerate(names)}
    any_ix = next(iter(cores.values()))
    profiles = []
    for c, ix in cores.items():
        ev = []
        for s0, s1, d, f in frames_by_core[c]:
            ev.append((ix.t_ns(s0), 0, d, fid[f]))
            ev.append((ix.t_ns(s1), 1, -d, fid[f]))
        ev.sort()
        events = [{'type': 'O' if k == 0 else 'C', 'frame': i, 'at': t} for t, k, _, i in ev]
        profiles.append({'type': 'evented', 'name': 'core %d' % c, 'unit': 'nanoseconds',
                         'startValue': events[0]['at'] if events else 0,
                         'endValue': events[-1]['at'] if events else 0, 'events': events})
    prof = {'$schema': 'https://www.speedscope.app/file-format-schema.json',
            'shared': {'frames': [{'name': any_ix.name(f)} for f in names]},
            'profiles': profiles, 'name': os.path.basename(a.snap.rstrip('/'))}
    out = a.o or os.path.join(a.snap, 'trace.speedscope.json')
    json.dump(prof, open(out, 'w'))
    print('%d profile(s) -> %s (open in https://www.speedscope.app, local)' % (len(profiles), out))

def cmd_folded(a):
    cores = load(a.snap, a.gips)
    agg = {}
    kinds = set()
    for c, ix in cores.items():
        kinds.add(_fold(ix, agg, a, 'core %d' % c if len(cores) > 1 else None))
    out = a.o or os.path.join(a.snap, 'trace.folded')
    with open(out, 'w') as fo:
        for k, v in sorted(agg.items(), key=lambda x: -x[1]):
            fo.write('%s %d\n' % (k, round(v)))
    print('%d stacks over %d core(s), weighted by %s -> %s (flamegraph.pl, or speedscope)' % (
        len(agg), len(cores), '/'.join(sorted(kinds)), out))


def _fold(ix, agg, a, prefix):
    stack = []
    cyc = len(ix.cc) >= 2 and not a.instructions   # weight by CPU cycles when the trace has them
    if cyc:   # one vectorised interpolation: np.interp on a scalar rescans the whole table each call
        ic_, n_ = ix.r[:, 3].astype(np.float64), ix.r[:, 2].astype(np.float64)
        weights = np.interp(ic_ + n_, ix.cc[:, 0], ix.cc[:, 1]) - np.interp(ic_, ix.cc[:, 0], ix.cc[:, 1])
    else:
        weights = ix.r[:, 2]
    for row, w in zip(ix.r, weights):
        st, en, n, ic, kind, depth, f, el = row
        del stack[depth:]
        while len(stack) < depth:
            stack.append(f)
        stack[depth - 1] = f
        key = ';'.join(([prefix] if prefix else []) + [ix.name(g) for g in stack])
        agg[key] = agg.get(key, 0) + float(w)
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
        st, en, n, ic, kind, depth, f, el = r[k]
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
  ./tracetool.py nav ss-t5-cc              step through the history (type h for the commands)
  ./tracetool.py perfetto ss-t6-6cores      six cores, one Perfetto track each
  ./tracetool.py nav ss-t6-6cores --core 3 step through core 3
  ./tracetool.py perfetto ss-t5-cc         then open ss-t5-cc/trace.perfetto.json in ui.perfetto.dev
  ./tracetool.py speedscope ss-t5-cc       then open ss-t5-cc/trace.speedscope.json in speedscope.app
  xdg-open ss-t5-cc/flamegraph.svg         the flame graph (any browser)
  ./tracetool.py index ss-t3-start --count 3000000    a new snapshot: index it first (3 M instructions)
'''


def main():
    if len(sys.argv) == 1:
        print(__doc__ + EXAMPLES)
        sys.exit(0)
    ap = argparse.ArgumentParser(epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('index'); p.add_argument('snap'); p.add_argument('--from', dest='start', type=int, default=0)
    p.add_argument('--count', type=int, default=0, help='instructions to keep (0 = all)')
    for c in ('perfetto', 'speedscope', 'folded'):
        p = sp.add_parser(c); p.add_argument('snap'); p.add_argument('-o'); p.add_argument('--gips', type=float, default=2.5)
        if c == 'folded':
            p.add_argument('--instructions', action='store_true', help='weight by instructions even with cycle counts')
    p = sp.add_parser('nav'); p.add_argument('snap'); p.add_argument('n', nargs='?'); p.add_argument('--gips', type=float, default=2.5)
    p.add_argument('--core', type=int, help='which core to step through (default: the lowest traced)')
    a = ap.parse_args()
    {'index': cmd_index, 'perfetto': cmd_perfetto, 'speedscope': cmd_speedscope, 'folded': cmd_folded,
     'nav': cmd_nav}[a.cmd](a)


if __name__ == '__main__':
    main()
