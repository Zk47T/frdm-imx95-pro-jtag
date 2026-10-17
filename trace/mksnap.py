#!/usr/bin/env python3
"""mksnap.py: an OpenCSD snapshot from an ETR buffer read over JTAG, then the decode and a check.

  mksnap.py --etr BUF.bin --base 0xf1000000 --rwp 0xf1d5b740 [--full] --log ETM_IDS_LOG --out DIR
            [--elf FILE ...]

- The buffer: if the ETR wrapped (--full), the oldest byte is at RWP: the buffer is rotated so
  that cstrace.bin starts there (OpenCSD wants it oldest first).
- The ETM: the 'etm N: TRC...=...' line that imx95-jtag.tcl's etm_ids prints (the registers as
  read from this ETM), plus TRCCONFIGR and TRCTRACEIDR as cs_trace_start programmed them.
- Memory images: every executable LOAD segment of vmlinux (nokaslr: link address = run address)
  and of each --elf (e.g. the static busybox that ran the workload), space ANY: Linux runs at
  EL2 here (VHE), user code at EL0.
Then trc_pkt_lister decodes it; the summary counts decode errors and memory misses, and turns the
instruction ranges into a per-function instruction count (System.map + the ELFs' symbols).
"""
import argparse, bisect, collections, os, re, subprocess, sys

OCSD = '/home/zk47/Project/5.elb1-t113/11.tmp/opencsd/trc_pkt_lister.sh'
KB = '/home/zk47/Learning/yocto-imx95-pro/frdm-imx95-pro/tmp/work/imx95_19x19_lpddr5_frdm_pro-poky-linux/linux-imx/6.18.20+git/build'
X = 'aarch64-linux-gnu-'


def exec_segments(elf):
    out = subprocess.run([X + 'readelf', '-lW', elf], capture_output=True, text=True).stdout
    segs = []
    for m in re.finditer(r'LOAD\s+(0x[0-9a-f]+) (0x[0-9a-f]+) 0x[0-9a-f]+ (0x[0-9a-f]+) 0x[0-9a-f]+ (.{3})', out):
        off, va, fsz, fl = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16), m.group(4)
        if 'E' in fl and fsz:
            segs.append((off, va, fsz))
    return segs


def symbols(elf=None):
    syms = []
    if elf is None:
        for l in open(KB + '/System.map'):
            a, t, n = l.split()[:3]
            if t in 'tTwW':
                syms.append((int(a, 16), n))
    else:
        out = subprocess.run([X + 'nm', elf], capture_output=True, text=True).stdout
        for l in out.splitlines():
            p = l.split()
            if len(p) == 3 and p[1] in 'tTwW':
                syms.append((int(p[0], 16), os.path.basename(elf) + ':' + p[2]))
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--etr', required=True)
    ap.add_argument('--base', type=lambda s: int(s, 0), required=True)
    ap.add_argument('--rwp', type=lambda s: int(s, 0), required=True)
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--log', required=True, help='a log with the etm_ids line')
    ap.add_argument('--configr', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--traceid', type=lambda s: int(s, 0), default=0x10)
    ap.add_argument('--elf', action='append', default=[])
    ap.add_argument('--cores', default='0', help='traced cores, e.g. 0,1,2,3 (trace id 0x10 + core when more than one)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--pids', help="'PID NAME' lines collected on the board while tracing (/proc/*/comm): "
                    'kept as pids.txt, tracetool names the tasks with it')
    ap.add_argument('--no-decode', action='store_true',
                    help='only the snapshot and decode-head.txt: a big capture goes to tracetool.py summary')
    ap.add_argument('--kernel-live', help='the running kernel text (physical _text, length of the first '
                    'vmlinux segment), read from DRAM: ocd.sh ktext')
    a = ap.parse_args()
    cores = [int(c) for c in a.cores.split(',')]
    os.makedirs(a.out, exist_ok=True)

    buf = open(a.etr, 'rb').read()
    off = a.rwp - a.base
    data = buf[off:] + buf[:off] if a.full else buf[:off]
    open(os.path.join(a.out, 'cstrace.bin'), 'wb').write(data)

    # one 'etm N: TRC...=...' line per core in the log (imx95-jtag.tcl etm_ids); the last one wins
    etm_lines = dict(re.findall(r'etm (\d+): (.*)', open(a.log).read()))
    for c in cores:
        ids = dict(re.findall(r'(TRC[A-Z0-9]+)=(0x[0-9a-f]+|0)\b', etm_lines[str(c)]))
        tid = a.traceid if len(cores) == 1 else 0x10 + c
        regs = {'TRCCONFIGR': a.configr, 'TRCTRACEIDR': tid}
        for k in ('TRCAUTHSTATUS', 'TRCIDR0', 'TRCIDR1', 'TRCIDR2', 'TRCIDR8', 'TRCIDR9', 'TRCIDR10', 'TRCIDR11',
                  'TRCIDR12', 'TRCIDR13'):
            regs[k] = int(ids[k], 16) if ids[k] != '0' else 0
        with open(os.path.join(a.out, 'etm_%d.ini' % c), 'w') as f:
            f.write('[device]\nname=etm_%d\nclass=trace_source\ntype=ETM4\n\n[regs]\n' % c)
            for k, v in regs.items():
                f.write('%s=0x%08X\n' % (k, v))

    dumps = []
    for elf in [KB + '/vmlinux'] + a.elf:
        raw = open(elf, 'rb').read()
        for i, (o, va, sz) in enumerate(exec_segments(elf)):
            name = '%s_%d.bin' % (os.path.basename(elf), i)
            img = raw[o:o + sz]
            if elf == KB + '/vmlinux' and i == 0 and a.kernel_live:
                # the kernel text as it runs: Linux patches itself at boot (alternatives, LSE atomics,
                # static keys: 0.4 % of the words, in 7,000 functions, here). Decoding against the
                # file follows branches that are NOPs now and loses returns (call stacks 500 deep).
                live = open(a.kernel_live, 'rb').read()
                if len(live) != sz:
                    sys.exit('%s: %d bytes, the text segment is %d' % (a.kernel_live, len(live), sz))
                print('kernel text: the live copy %s (%d words differ from vmlinux)' % (
                    a.kernel_live, sum(1 for j in range(0, sz, 4) if live[j:j + 4] != img[j:j + 4])))
                img = live
            open(os.path.join(a.out, name), 'wb').write(img)
            dumps.append((name, va, sz))
    for c in cores:
        with open(os.path.join(a.out, 'cpu_%d.ini' % c), 'w') as f:
            f.write('[device]\nname=cpu_%d\nclass=core\ntype=Cortex-A55\n\n[regs]\nPC(size:64)=0\n' % c)
            for i, (name, va, sz) in enumerate(dumps):
                f.write('\n[dump%d]\nfile=%s\naddress=0x%X\nlength=0x%X\nspace=ANY\n' % (i + 1, name, va, sz))
    if a.pids:
        with open(os.path.join(a.out, 'pids.txt'), 'w') as f:
            f.writelines(l for l in open(a.pids) if re.match(r'\d+ \S', l))
    devs = ''.join('device%d=cpu_%d.ini\ndevice%d=etm_%d.ini\n' % (2 * i, c, 2 * i + 1, c) for i, c in enumerate(cores))
    open(os.path.join(a.out, 'snapshot.ini'), 'w').write(
        '[snapshot]\nversion=1.0\ndescription=i.MX95 A55 cores %s ETMv4 -> ETF -> ETR, read over JTAG\n\n'
        '[device_list]\n%s\n[trace]\nmetadata=trace.ini\n' % (a.cores, devs))
    open(os.path.join(a.out, 'trace.ini'), 'w').write(
        '[trace_buffers]\nbuffers=buffer0\n\n[buffer0]\nname=etr_0\nfile=cstrace.bin\nformat=coresight\n\n'
        '[source_buffers]\n' + ''.join('etm_%d=etr_0\n' % c for c in cores) +
        '\n[core_trace_sources]\n' + ''.join('cpu_%d=etm_%d\n' % (c, c) for c in cores))
    print('snapshot in %s: %d bytes of trace (%s), %d memory images' % (
        a.out, len(data), 'rotated at RWP' if a.full else 'not wrapped', len(dumps)))

    # the full decode is ~300 MB per MB of trace: stream it, keep the first lines and the summary
    proc = subprocess.Popen([OCSD, '-ss_dir', a.out, '-decode_only', '-logstdout', '-no_time_print'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    head = open(os.path.join(a.out, 'decode-head.txt'), 'w')
    syms = sorted(symbols() + [s for e in a.elf for s in symbols(e)])
    addrs = [s[0] for s in syms]

    def name(pc):
        i = bisect.bisect_right(addrs, pc) - 1
        return syms[i][1] if i >= 0 and pc - syms[i][0] < 0x200000 else '%#x' % pc

    kinds = collections.Counter()
    fn = collections.Counter()
    el = collections.Counter()
    total = 0
    per_id = collections.Counter()
    sync_id = collections.Counter()
    rng = re.compile(r'INSTR_RANGE\(exec range=0x([0-9a-f]+):\[0x([0-9a-f]+)\] num_i\((\d+)\)')
    ctx = re.compile(r'PE_CONTEXT\(\(ISA=\w+\) (EL\d)(\w)')
    for ln, line in enumerate(proc.stdout):
        if ln < 2000:
            head.write(line)
        elif a.no_decode:
            proc.kill()
            head.close()
            print('decode skipped (--no-decode): tracetool.py summary %s' % a.out)
            return
        m = re.search(r'OCSD_GEN_TRC_ELEM_(\w+)', line)
        if m:
            kinds[m.group(1)] += 1
        if 'OCSD_ERR' in line or 'ERROR' in line.upper() and 'ERR_' in line:
            kinds['error-lines'] += 1
        idm = re.search(r'ID:([0-9a-f]+);', line)
        if idm and ('NO_SYNC' in line or 'OVERFLOW' in line.upper()):
            sync_id[int(idm.group(1), 16)] += 1
        m = rng.search(line)
        if m:
            n = int(m.group(3))
            total += n
            if idm:
                per_id[int(idm.group(1), 16)] += n
            fn[name(int(m.group(1), 16))] += n
        m = ctx.search(line)
        if m:
            el[m.group(1) + m.group(2)] += 1
    proc.wait()
    head.close()
    print('decoder elements:', dict(kinds.most_common(12)))
    print('PE contexts seen:', dict(el))
    if len(cores) > 1:
        for t in sorted(set(per_id) | set(sync_id)):
            print('  trace id %#x (core %d): %11d instructions, %4d NO_SYNC/overflow' % (t, t - 0x10, per_id[t], sync_id[t]))
    print('%d instructions decoded; top functions by instructions executed:' % total)
    for n, c in fn.most_common(25):
        print('  %6.2f %%  %s' % (100.0 * c / max(total, 1), n))


if __name__ == '__main__':
    main()
