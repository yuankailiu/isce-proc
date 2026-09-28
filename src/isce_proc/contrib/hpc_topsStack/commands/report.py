"""`topsstack.py report`: per-step time, CPU, memory, cost and disk use, from sacct.

Job IDs come from run_files/job_id_logfile_*.txt (all submissions) plus any extra IDs given
(e.g. reruns). Nothing is read from the per-task timings.txt appends.
"""
import csv
import glob
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime

from commands.data import _log


def _bytes(x):
    m = re.fullmatch(r'([\d.]+)([KMGTP]?)', x or '')
    return float(m[1]) * 1024 ** 'KMGTP'.index(m[2]) * 1024 if m and m[2] else (float(m[1]) if m else 0.0)


def _sec(t):
    """sacct [DD-]HH:MM:SS[.fff] or MM:SS.fff -> seconds."""
    if not t or t in ('INVALID', 'Unknown'):
        return 0.0
    d, _, t = t.rpartition('-')
    parts = [float(x) for x in t.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return (int(d) if d else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def _job_ids(run_files):
    """(step, jobid) in submission order from all job_id_logfile_*.txt."""
    out = []
    for f in sorted(glob.glob(os.path.join(run_files, 'job_id_logfile_*.txt')), key=os.path.getmtime):
        out += re.findall(r'^(run_\d+_\w+?)\s*(\d{6,})\s*$', open(f).read(), re.M)   # older logs: no space
    return out


def report(c, extra_ids=()):
    run_files = os.path.join(c.stack, 'run_files')
    ids = _job_ids(run_files)
    name_of = {j: s for s, j in ids}
    all_ids = [j for _, j in ids] + list(extra_ids)
    if not all_ids:
        print('no job IDs found (run_files/job_id_logfile_*.txt)')
        return 1
    rows = []
    for k in range(0, len(all_ids), 50):
        out = subprocess.run(['sacct', '-j', ','.join(all_ids[k:k + 50]), '--noheader', '--parsable2',
                              '--format=JobID,JobName%60,State,Start,End,ElapsedRaw,TotalCPU,AllocCPUS,MaxRSS,ReqMem,AllocTRES%120'],
                             capture_output=True, text=True).stdout
        rows += [r.split('|') for r in out.splitlines() if r]
    # one record per array task: state/time from the task line, MaxRSS = max over its steps
    task = {}
    for jid, name, state, start, end, el, cpu, ncpu, rss, req, tres in rows:
        base = jid.split('.')[0]
        t = task.setdefault(base, {'rss': 0.0, 'cpu': 0.0})
        if '.' not in jid:
            array = base.split('_')[0]
            step = name_of.get(array) or re.sub(r'^\d+_', '', name).rsplit('_', 1)[0]
            gpu = re.search(r'gres/gpu[^=,]*=(\d+)', tres)
            t.update(step=step, state=state.split()[0], start=start, end=end, wall=float(el or 0),
                     ncpu=int(ncpu or 1), ngpu=int(gpu[1]) if gpu else 0, req=req)
        else:                                                   # CPU time is recorded on the steps
            t['cpu'] += _sec(cpu)
        t['rss'] = max(t['rss'], _bytes(rss))
    per = defaultdict(lambda: {'n': 0, 'states': defaultdict(int), 'start': None, 'end': None,
                               'cpu': 0.0, 'alloc': 0.0, 'gpu': 0.0, 'rss': 0.0, 'req': ''})
    for t in task.values():
        if 'step' not in t:
            continue
        p = per[t['step']]
        p['n'] += 1
        p['states'][t['state']] += 1
        for key, fn in (('start', min), ('end', max)):
            v = t[key]
            if v and v not in ('Unknown', 'None'):
                p[key] = fn(p[key], v) if p[key] else v
        p['cpu'] += t['cpu']
        p['alloc'] += t['wall'] * t['ncpu']
        p['gpu'] += t['wall'] * t['ngpu']
        p['rss'] = max(p['rss'], t['rss'])
        p['req'] = t['req']
    sizes = {}
    f = os.path.join(run_files, 'total_file_sizes.txt')
    if os.path.isfile(f):
        for line in open(f).read().splitlines()[1:]:
            w = line.split()
            if len(w) >= 5:
                sizes[w[1]] = w[4]                              # step number -> size after (or during) it
    fmt = lambda s: datetime.fromisoformat(s) if s else None
    lines = [f'{"step":36s} {"tasks":>6s} {"states":28s} {"wall span":>10s} {"CPU h":>8s} {"alloc CPU h":>11s} '
             f'{"GPU h":>6s} {"cost $":>7s} {"max RSS":>8s} {"req mem":>8s} {"size":>6s}']
    tot = {'cpu': 0.0, 'alloc': 0.0, 'gpu': 0.0}
    units = lambda p: p['alloc'] / 3600 + p['gpu'] / 3600 * c.hpc.gpuUnits   # compute units
    out_csv = []
    for step in sorted(per, key=lambda s: int(re.match(r'run_(\d+)', s)[1]) if re.match(r'run_\d+', s) else 999):
        p = per[step]
        span = (fmt(p['end']) - fmt(p['start'])).total_seconds() / 3600 if p['start'] and p['end'] else 0
        states = ','.join(f'{k}:{v}' for k, v in sorted(p['states'].items()))
        cost = units(p) * c.hpc.costPerCpuHour
        size = sizes.get(step[:6], '')
        lines.append(f'{step:36s} {p["n"]:6d} {states[:28]:28s} {span:9.1f}h {p["cpu"] / 3600:8.1f} '
                     f'{p["alloc"] / 3600:11.1f} {p["gpu"] / 3600:6.1f} {cost:7.2f} {p["rss"] / 2**30:7.1f}G {p["req"]:>8s} {size:>6s}')
        tot['cpu'] += p['cpu']
        tot['alloc'] += p['alloc']
        tot['gpu'] += p['gpu']
        out_csv.append({'step': step, 'tasks': p['n'], 'states': states, 'start': p['start'], 'end': p['end'],
                        'wall_span_h': round(span, 3), 'cpu_h': round(p['cpu'] / 3600, 3),
                        'alloc_cpu_h': round(p['alloc'] / 3600, 3), 'gpu_h': round(p['gpu'] / 3600, 3),
                        'cost_usd': round(cost, 2),
                        'max_rss_GB': round(p['rss'] / 2**30, 2), 'req_mem': p['req'], 'size_after': size})
    lines.append(f'{"total":36s} {"":6s} {"":28s} {"":>10s} {tot["cpu"] / 3600:8.1f} {tot["alloc"] / 3600:11.1f} '
                 f'{tot["gpu"] / 3600:6.1f} {units(tot) * c.hpc.costPerCpuHour:7.2f}')
    if tot['alloc'] and tot['cpu'] < 0.01 * tot['alloc']:
        lines.append('CPU h: this cluster\'s accounting records (almost) no CPU time for job steps, so only '
                     'alloc CPU h is meaningful')
    lines.append('tasks, states and wall span cover all submissions of a step (reruns included); '
                 'wall span = first start to last end; alloc CPU h = elapsed x allocated CPUs; '
                 f'cost = (alloc CPU h + GPU h x {c.hpc.gpuUnits:g}) x ${c.hpc.costPerCpuHour:g} per compute unit '
                 '(hpc.costPerCpuHour, hpc.gpuUnits)')
    text = '\n'.join(lines)
    print(text)
    os.makedirs(os.path.join(c.stack, 'logs'), exist_ok=True)
    stamp = f'{datetime.now():%Y-%m-%d}'
    with open(os.path.join(c.stack, 'logs', f'report_{stamp}.txt'), 'w') as f:
        f.write(text + '\n')
    with open(os.path.join(c.stack, 'logs', f'report_{stamp}.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out_csv[0]) if out_csv else ['step'])
        w.writeheader()
        w.writerows(out_csv)
    print(f'-> logs/report_{stamp}.txt, .csv')
    return 0
