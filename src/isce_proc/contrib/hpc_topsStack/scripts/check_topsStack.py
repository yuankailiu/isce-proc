#!/usr/bin/env python3
"""Check which rows of each topsStack step produced their outputs, and rerun the rest.

Slurm COMPLETED is not proof: a task can exit 0 without writing anything. This reads
the expected outputs of every row (from its SentinelWrapper config or command-line
flags), checks them on disk (existence; size == width x length x bands x type from
the .xml), and reports the bad rows with a hint from the row's latest log.

A binary that is missing while its .xml still exists is reported as "cleaned":
ISCE writes the .xml after the data, and the cleanup tools delete binaries only.

Run from the stack directory or its run_files/:
  check_topsStack.py 13-16                 # report steps 13 to 16
  check_topsStack.py 15 --rerun            # also print the sbatch commands for bad rows
  check_topsStack.py 15 --rerun --submit   # submit them, and make the next queued
                                           # step wait for them (job_id_logfile_*.txt)
"""
import argparse, glob, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

MAX_ARRAY = 1000   # rows per .pN job file, as in write_slurmJobs.py
# output keys per SentinelWrapper function; other functions are checked by log only
CFG_OUT = {'generateIgram': ['interferogram'], 'mergeBursts': ['outfile'], 'mergeBurstsIon': ['outfile'],
           'FilterAndCoherence': ['filt', 'coh', 'complex_coh'], 'unwrap': ['unw'],
           'coherenceIon': ['coherence'], 'lookUnwIon': ['output'], 'filtIon': ['output'],
           'filtIonShift': ['output'], 'burstRampIon': ['output']}
CLI_OUT = ['--output', '--odir', '--ionosphere', '--coherence_output']
DTYPE = {'CFLOAT': 8, 'FLOAT': 4, 'BYTE': 1, 'DOUBLE': 8, 'CDOUBLE': 16, 'SHORT': 2, 'INT': 4}
LOG_ERR = re.compile(r'Traceback|srun: error|Exit status: [1-9]|DUE TO TIME LIMIT|CANCELLED AT|'
                     r'Killed|oom-kill|Disk quota exceeded|ERROR 4')


def outputs(cmd):
    """Expected output paths of one run_file row; (path, kind) with kind 'file' or 'igram_dir'."""
    w = cmd.split()
    if w and w[0] == 'SentinelWrapper.py' and '-c' in w:
        out = []
        for block in re.split(r'\[Function-\d+\]', open(w[w.index('-c') + 1]).read())[1:]:
            kv = dict(re.findall(r'^[ \t]*(\w+)[ \t]*:[ \t]*(.*?)[ \t]*$', block, re.M))
            func = next((k for k, v in kv.items() if v == ''), None)
            for key in CFG_OUT.get(func, []):
                if not kv.get(key):
                    continue
                if func == 'mergeBursts' and kv.get('multilook') != 'True':
                    # without multilooking only <outfile>.full is written, as a VRT if use_virtual_files
                    full = kv[key] + '.full'
                    out.append((full + '.vrt', 'file') if kv.get('use_virtual_files') == 'True' else (full, 'file'))
                else:
                    out.append((kv[key], 'igram_dir' if func == 'generateIgram' else 'file'))
            if func == 'unwrap' and kv.get('method', 'snaphu') == 'snaphu' and kv.get('unw'):
                out.append((kv['unw'] + '.conncomp', 'file'))
        return out
    return [(w[i + 1], 'file') for i, x in enumerate(w[:-1]) if x in CLI_OUT]


def check_file(p):
    """'' if OK, else a short reason."""
    if os.path.isdir(p):
        return '' if os.listdir(p) else 'empty dir'
    if not os.path.exists(p):
        return 'cleaned' if os.path.exists(p + '.xml') else 'missing'
    if not os.path.exists(p + '.xml'):
        return '' if os.path.getsize(p) > 0 else 'empty'
    s = open(p + '.xml').read()
    g = lambda k: re.search(rf'<property name="{k}">\s*<value>([^<]+)', s)
    try:
        exp = int(g('width').group(1)) * int(g('length').group(1)) * int(g('number_bands').group(1)) \
              * DTYPE[g('data_type').group(1).strip().upper()]
    except (AttributeError, KeyError):
        return ''                                     # xml without image size: existence only
    return '' if os.path.getsize(p) == exp else f'size {os.path.getsize(p)} != {exp}'


def check_row(cmd):
    bad = []
    for path, kind in outputs(cmd):
        if kind == 'igram_dir':
            ints = glob.glob(os.path.join(path, 'IW*', 'fine_*.int')) + glob.glob(os.path.join(path, 'IW*', 'fine_*.int.xml'))
            files = sorted({f[:-4] if f.endswith('.xml') else f for f in ints})
            if not files:
                bad.append(f'{path}: no burst interferograms')
            bad += [f'{f}: {r}' for f in files for r in [check_file(f)] if r]
        else:
            r = check_file(path)
            if r:
                bad.append(f'{path}: {r}')
    return bad


def latest_logs(step):
    """row -> (log file, 'ok' / 'error' / 'unfinished') from the most recent log of each row."""
    best = {}
    for f in glob.glob(f'slurm-{step}-*_*.out'):
        m = re.search(r'_(\d+)(?:\.p(\d+))?\.out$', f)
        row = int(m.group(1)) + MAX_ARRAY * (int(m.group(2) or 1) - 1)
        if row not in best or os.path.getmtime(f) > os.path.getmtime(best[row]):
            best[row] = f
    res = {}
    for row, f in best.items():
        txt = open(f, errors='replace').read()
        res[row] = (f, 'error' if LOG_ERR.search(txt) else 'ok' if 'Total elapsed' in txt else 'unfinished')
    return res


def step_ids(step):
    """Job IDs of a step from the most recent job_id_logfile_*.txt, in submission order."""
    logs = sorted(glob.glob('job_id_logfile_*.txt'), key=os.path.getmtime)
    return re.findall(rf'^{step}\s+(\d+)', open(logs[-1]).read(), re.M) if logs else []


def parse_steps(args, runs):
    nums = sorted(runs)
    if not args:
        return nums
    sel = set()
    for a in args:
        lo, _, hi = a.partition('-')
        sel |= {n for n in nums if int(lo) <= n <= int(hi or lo)}
    return sorted(sel)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('steps', nargs='*', help='step numbers or ranges, e.g. 13 15-16 (default: all)')
    ap.add_argument('--rerun', action='store_true', help='print sbatch commands for the bad rows')
    ap.add_argument('--submit', action='store_true', help='with --rerun: submit them and re-link the next step')
    ap.add_argument('--cleaned-ok', action='store_true', help='count "cleaned" outputs as OK (default: reported)')
    ap.add_argument('-n', '--nproc', type=int, default=32)
    ap.add_argument('-v', '--verbose', action='store_true', help='list every bad row')
    args = ap.parse_args()

    if os.path.isdir('run_files'):
        os.chdir('run_files')
    if os.path.basename(os.getcwd()) != 'run_files':
        sys.exit('run from the stack directory or its run_files/')
    runs = {int(m.group(1)): f for f in os.listdir('.') for m in [re.fullmatch(r'run_(\d+)_\w+', f)] if m}
    steps = parse_steps(args.steps, runs)

    for i, n in enumerate(steps):
        step = runs[n]
        cmds = [l.strip() for l in open(step) if l.strip()]
        with ThreadPoolExecutor(args.nproc) as ex:
            probs = list(ex.map(check_row, cmds))
        logs = latest_logs(step)
        rows = {}
        for r, p in enumerate(probs, 1):
            log = logs.get(r, (None, 'no log'))
            if args.cleaned_ok:
                p = [x for x in p if not x.endswith(': cleaned')]
            if p or log[1] == 'error' and not outputs(cmds[r - 1]):
                rows[r] = (p, log)
        n_cleaned = sum(1 for p, _ in rows.values() if p and all(x.endswith(': cleaned') for x in p))
        bad = {r: v for r, v in rows.items() if not (v[0] and all(x.endswith(': cleaned') for x in v[0]))}
        checked = 'outputs' if any(outputs(c) for c in cmds[:3]) else 'logs only'
        print(f'{step:40s} rows {len(cmds):5d}  bad {len(bad):5d}  cleaned {n_cleaned:5d}  ({checked})')
        for r, (p, (f, st)) in list(bad.items())[:None if args.verbose else 5]:
            print(f'    row {r:5d}  log: {st:10s} {f or "-"}\n              {p[0] if p else "log shows an error"}'
                  + (f'  (+{len(p) - 1} more)' if len(p) > 1 else ''))
        if not (args.rerun and bad):
            continue

        # rerun the bad rows of this step, per .pN job file
        parts = {}
        for r in sorted(bad):
            parts.setdefault((r - 1) // MAX_ARRAY + 1, []).append((r - 1) % MAX_ARRAY + 1)
        multi = os.path.exists(f'{step}.p1.job')
        ids = step_ids(step)
        active = ids and subprocess.run(['squeue', '-h', '-j', ','.join(ids)], capture_output=True, text=True).stdout.strip()
        submit = args.submit and not active
        if args.submit and active:
            print(f'    not submitting: {step} still has queued/running tasks (a rerun would duplicate them)')
        new_ids = []
        for p, tasks in parts.items():
            job = f'{step}.p{p}.job' if multi else f'{step}.job'
            cmd = ['sbatch', '--parsable', f'--array={",".join(map(str, tasks))}', job]
            print('    ' + ' '.join(cmd))
            if submit:
                new_ids.append(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())
        if submit:
            print(f'    submitted {new_ids}')
            nxt = next((runs[m] for m in sorted(runs) if m > n), None)
            nxt_ids = step_ids(nxt) if nxt else []
            if nxt_ids:
                dep = ','.join([f'afterany:{j}' for j in step_ids(step)] + [f'afterok:{j}' for j in new_ids])
                r = subprocess.run(['scontrol', 'update', f'jobid={nxt_ids[0]}', f'dependency={dep}'],
                                   capture_output=True, text=True)
                print(f'    {nxt} ({nxt_ids[0]}) now waits for: {dep}' if r.returncode == 0 else
                      f'    could not re-link {nxt} ({nxt_ids[0]}): {r.stderr.strip()}')


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    main()
