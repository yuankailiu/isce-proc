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
  check_topsStack.py 15 --gate 2           # (gate.job) rerun up to 2x via follow-up gates, exit 1 if still bad
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
                elif func == 'generateIgram':
                    out.append((kv[key], 'igram_dir', (kv.get('reference'), kv.get('secondary'))))
                else:
                    out.append((kv[key], 'file'))
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
        w, l = int(g('width').group(1)), int(g('length').group(1))
        exp = w * l * int(g('number_bands').group(1)) * DTYPE[g('data_type').group(1).strip().upper()]
    except (AttributeError, KeyError):
        return '' if os.path.getsize(p) > 0 else 'empty'   # xml without image size: existence only
    if exp == 0:
        return f'empty image ({w} x {l} in .xml)'         # e.g. a filter that produced no lines
    return '' if os.path.getsize(p) == exp else f'size {os.path.getsize(p)} != {exp}'


def bursts(d):
    """{(swath, burst number)} of a reference/ or coreg_secondarys/<date>/ dir, from the burst .xml files."""
    return {(os.path.basename(os.path.dirname(x)), os.path.basename(x)[6:8])
            for x in glob.glob(os.path.join(d, 'IW*', 'burst_[0-9][0-9].slc.xml'))} if d else set()


def check_row(cmd):
    bad = []
    for path, kind, *meta in outputs(cmd):
        if kind == 'igram_dir':
            # every burst present in both the reference and the secondary must have its interferogram
            expected = bursts(meta[0][0]) & bursts(meta[0][1]) if meta else set()
            ints = glob.glob(os.path.join(path, 'IW*', 'fine_*.int')) + glob.glob(os.path.join(path, 'IW*', 'fine_*.int.xml'))
            files = {f[:-4] if f.endswith('.xml') else f for f in ints}
            files |= {os.path.join(path, sw, f'fine_{b}.int') for sw, b in expected}
            if not files:
                bad.append(f'{path}: no burst interferograms')
            bad += [f'{f}: {r}' for f in sorted(files) for r in [check_file(f)] if r]
        else:
            r = check_file(path)
            if r:
                bad.append(f'{path}: {r}')
    return bad


def part_offsets(step):
    """{part suffix ('' or 'pN'): (row offset, number of tasks)} from the step's .job files
    (parts may be uneven: write_slurmJobs.py also splits at program changes)."""
    out = {}
    for job in glob.glob(f'{step}.job') + glob.glob(f'{step}.p*.job'):
        m = re.fullmatch(rf'{re.escape(step)}(?:\.(p\d+))?\.job', job)
        s = open(job).read()
        off = re.search(r'ROWINDEX=\$\(\(SLURM_ARRAY_TASK_ID\+(\d+)\)\)', s)
        arr = re.search(r'#SBATCH --array=1-(\d+)', s)
        if m and off and arr:
            out[m.group(1) or ''] = (int(off.group(1)), int(arr.group(1)))
    return out


def latest_logs(step):
    """row -> (log file, 'ok' / 'error' / 'unfinished') from the most recent log of each row."""
    best = {}
    offsets = part_offsets(step)
    for f in glob.glob(f'slurm-{step}-*_*.out'):
        m = re.search(r'_(\d+)(?:\.(p\d+))?\.out$', f)
        off = offsets.get(m.group(2) or '', (MAX_ARRAY * (int((m.group(2) or 'p1')[1:]) - 1), 0))[0]
        row = int(m.group(1)) + off
        if row not in best or os.path.getmtime(f) > os.path.getmtime(best[row]):
            best[row] = f
    res = {}
    for row, f in best.items():
        txt = open(f, errors='replace').read()
        res[row] = (f, 'error' if LOG_ERR.search(txt) else 'ok' if 'Total elapsed' in txt else 'unfinished')
    return res


def step_ids(step):
    """Job IDs of a step from the most recent job_id_logfile_*.txt that lists it, in submission order.

    A log may hold several submissions (appended); the last block listing the step wins.
    """
    for log in sorted(glob.glob('job_id_logfile_*.txt'), key=os.path.getmtime, reverse=True):
        blocks = re.split(r'^IDs of Jobs submitted at:.*$', open(log).read(), flags=re.M)
        for b in reversed(blocks):
            ids = re.findall(rf'^{re.escape(step)}\s*(\d+)\s*$', b, re.M)   # older logs: no space
            if ids:
                return ids
    return []


def parse_steps(args, runs):
    nums = sorted(runs)
    if not args:
        return nums
    sel = set()
    for a in args:
        lo, _, hi = a.partition('-')
        sel |= {n for n in nums if int(lo) <= n <= int(hi or lo)}
    return sorted(sel)


def evaluate(step, args, strict=False):
    """(cmds, bad, n_cleaned) for one step; print a summary. strict: rows of log-only steps need an OK log."""
    cmds = [l.strip() for l in open(step) if l.strip()]
    with ThreadPoolExecutor(args.nproc) as ex:
        probs = list(ex.map(check_row, cmds))
    logs = latest_logs(step)
    has_out = [bool(outputs(c)) for c in cmds]
    rows = {}
    for r, p in enumerate(probs, 1):
        log = logs.get(r, (None, 'no log'))
        if args.cleaned_ok:
            p = [x for x in p if not x.endswith(': cleaned')]
        if p or (not has_out[r - 1] and (log[1] == 'error' or (strict and log[1] != 'ok'))):
            rows[r] = (p, log)
    n_cleaned = sum(1 for p, _ in rows.values() if p and all(x.endswith(': cleaned') for x in p))
    bad = {r: v for r, v in rows.items() if not (v[0] and all(x.endswith(': cleaned') for x in v[0]))}
    checked = 'outputs' if any(has_out[:3]) else 'logs only'
    print(f'{step:40s} rows {len(cmds):5d}  bad {len(bad):5d}  cleaned {n_cleaned:5d}  ({checked})')
    for r, (p, (f, st)) in list(bad.items())[:None if args.verbose else 5]:
        print(f'    row {r:5d}  log: {st:10s} {f or "-"}\n              {p[0] if p else "log: " + st}'
              + (f'  (+{len(p) - 1} more)' if len(p) > 1 else ''))
    return cmds, bad, n_cleaned


def submit_reruns(step, bad, submit):
    """sbatch the bad rows, per .pN job file; return new job IDs (none if not submitting)."""
    offsets = part_offsets(step) or {'': (0, MAX_ARRAY)}
    parts = {}
    for r in sorted(bad):
        for sfx, (off, n) in offsets.items():
            if off < r <= off + n:
                parts.setdefault(sfx, []).append(r - off)
                break
    new_ids = []
    for sfx, tasks in sorted(parts.items()):
        job = f'{step}.{sfx}.job' if sfx else f'{step}.job'
        cmd = ['sbatch', '--parsable', f'--array={",".join(map(str, tasks))}', job]
        print('    ' + ' '.join(cmd))
        if submit:
            new_ids.append(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())
    if submit:
        print(f'    submitted {new_ids}')
    return new_ids


def active(ids):
    return bool(ids) and bool(subprocess.run(['squeue', '-h', '-j', ','.join(ids)],
                                             capture_output=True, text=True).stdout.strip())


def relink_next(runs, n, step, new_ids):
    """Make the next queued step wait for the reruns as well (afterany: this step, afterok: reruns)."""
    nxt = next((runs[m] for m in sorted(runs) if m > n), None)
    nxt_ids = step_ids(nxt) if nxt else []
    if nxt_ids:
        dep = ','.join([f'afterany:{j}' for j in step_ids(step)] + [f'afterok:{j}' for j in new_ids])
        r = subprocess.run(['scontrol', 'update', f'jobid={nxt_ids[0]}', f'dependency={dep}'],
                           capture_output=True, text=True)
        print(f'    {nxt} ({nxt_ids[0]}) now waits for: {dep}' if r.returncode == 0 else
              f'    could not re-link {nxt} ({nxt_ids[0]}): {r.stderr.strip()}')


def gate(step, args, retries):
    """Gate job between two steps (gate.job, afterany on all parts of `step`). Exits in minutes:
    - all rows good                      -> exit 0 (the next step, afterok on this gate, starts)
    - bad rows, attempt < retries        -> submit the reruns and a follow-up gate (afterany on
      them), move the next step from afterok:<this gate> to afterok:<follow-up gate>, exit 0
    - bad rows, no retries left          -> exit 1 (the next step never starts; FAIL mail)
    Short gates start quickly (backfill); waiting for reruns is left to Slurm dependencies."""
    attempt = int(os.environ.get('GATE_ATTEMPT', '0'))
    _, bad, _ = evaluate(step, args, strict=True)
    if not bad:
        print(f'gate {step}: all rows good')
        return 0
    if attempt >= retries:
        print(f'gate {step}: {len(bad)} rows still bad after {retries} reruns; stopping the chain')
        return 1
    me = os.environ.get('SLURM_JOB_ID')
    print(f'gate {step}: rerun {attempt + 1}/{retries} of {len(bad)} rows')
    ids = submit_reruns(step, bad, submit=True)
    nxt = subprocess.run(['sbatch', '--parsable', f'--dependency=afterany:{":".join(ids)}',
                          f'--export=ALL,STEP={step},GATE_ATTEMPT={attempt + 1}', 'gate.job'],
                         capture_output=True, text=True, check=True).stdout.strip()
    with open(sorted(glob.glob('job_id_logfile_*.txt'), key=os.path.getmtime)[-1], 'a') as f:
        f.write(f'{"gate_" + step:50s} {nxt}\n')
    moved = []
    if me:                                             # jobs waiting afterok on this gate
        q = subprocess.run(['squeue', '-h', '-u', os.environ.get('USER', ''), '-o', '%i %E'],
                           capture_output=True, text=True).stdout.splitlines()
        for line in q:
            jid, _, dep = line.partition(' ')
            if f'afterok:{me}' in dep:
                subprocess.run(['scontrol', 'update', f'jobid={jid.split("_")[0]}', f'dependency=afterok:{nxt}'],
                               capture_output=True, text=True)
                moved.append(jid.split('_')[0])
    print(f'gate {step}: follow-up gate {nxt} waits for {ids}; moved {sorted(set(moved))} to afterok:{nxt}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('steps', nargs='*', help='step numbers or ranges, e.g. 13 15-16 (default: all)')
    ap.add_argument('--rerun', action='store_true', help='print sbatch commands for the bad rows')
    ap.add_argument('--submit', action='store_true', help='with --rerun: submit them and re-link the next step')
    ap.add_argument('--gate', type=int, metavar='N', help='gate mode (in gate.job): rerun bad rows up to N times '
                    '(each via a follow-up gate job), exit non-zero if rows are still bad')
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

    if args.gate is not None:
        if len(steps) != 1:
            sys.exit('--gate takes exactly one step')
        return gate(runs[steps[0]], args, args.gate)

    for n in steps:
        step = runs[n]
        _, bad, _ = evaluate(step, args)
        if not (args.rerun and bad):
            continue
        busy = active(step_ids(step))
        if args.submit and busy:
            print(f'    not submitting: {step} still has queued/running tasks (a rerun would duplicate them)')
        new_ids = submit_reruns(step, bad, submit=args.submit and not busy)
        if new_ids:
            relink_next(runs, n, step, new_ids)


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
