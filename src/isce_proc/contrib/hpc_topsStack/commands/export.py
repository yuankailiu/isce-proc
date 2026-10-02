"""`topsstack.py export`: copy the products MintPy needs (and the run records) to hpc.exportDir.

Replaces copy_outputs.sh: target from the template, no prompts, parallel rsync, --dry-run.
Items that do not exist (e.g. no ionosphere steps) are skipped.
"""
import glob
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from commands.data import _log

# (source glob relative to the stack dir, extra rsync options)
ITEMS = [
    ('reference/IW*.xml', []),                       # stack metadata
    ('baselines', []),
    ('merged/interferograms/*', []),                  # one directory per pair: split into nproc rsync jobs
    ('ion/*/ion_cal', []),
    ('ion_dates', []),
    ('ion_azshift_dates', []),
    ('ion_burst_ramp_merged_dates', []),
    ('configs', []),
    ('run_files', ['--exclude', 'preselect/']),
    ('logs', []),
    ('pic', []),
    ('inputs', []),
    ('*.txt', []), ('*.log', []), ('*.md', []), ('*.pdf', []), ('*.png', []),
]


def export(c, dry_run=False, nproc=4, run_files_to=None):
    target = c.hpc.exportDir
    if not target:
        sys.exit('set hpc.exportDir in the template (target directory for the copy)')
    if not dry_run:
        os.makedirs(target, exist_ok=True)
    jobs = []
    for pattern, opts in ITEMS:
        if pattern == 'run_files' and run_files_to:            # e.g. an update stack: keep the target's run_files/
            jobs.append((f'run_files -> {run_files_to}', ['rsync', '-a', *(['-n'] if dry_run else []), '--stats', *opts,
                                                          'run_files/', os.path.join(target, run_files_to) + '/']))
            continue
        srcs = sorted(glob.glob(os.path.join(c.stack, pattern)))
        if srcs:
            rel = [os.path.relpath(s, c.stack) for s in srcs]
            parts = [rel[i::nproc] for i in range(nproc)] if len(rel) > 50 else [rel]   # big lists in parallel
            for i, part in enumerate(p for p in parts if p):
                name = pattern if len(parts) == 1 else f'{pattern} [{i + 1}/{len(parts)}]'
                jobs.append((name, ['rsync', '-aR', *(['-n'] if dry_run else []), '--stats', *opts, *part, target + '/']))
    geom = os.path.join(c.stack, 'merged', 'geom_reference')      # -> geom_reference/ on top (as on marmot)
    if os.path.isdir(geom):
        jobs.append(('geom_reference', ['rsync', '-a', *(['-n'] if dry_run else []), '--stats', '--exclude', '*.full*',
                                        geom + '/', os.path.join(target, 'geom_reference') + '/']))
    data_dst = os.path.join(target, 'data') + '/'                  # data-dir records, not the zips
    jobs.append(('data (no zips)', ['rsync', '-a', *(['-n'] if dry_run else []), '--stats', '--exclude', '*.zip',
                                    '--exclude', 'not_used/', c.data + '/', data_dst]))

    def run(job):
        name, cmd = job
        r = subprocess.run(cmd, cwd=c.stack, capture_output=True, text=True)
        stat = dict(l.split(':', 1) for l in r.stdout.splitlines() if ':' in l)
        num = lambda k: int(stat.get(k, '0').split()[0].replace(',', '') or 0)
        return name, r.returncode, num('Total transferred file size'), num('Total file size'), r.stderr.strip()[-300:]

    def human(n):
        for u in ('B', 'KB', 'MB', 'GB', 'TB'):
            if n < 1024 or u == 'TB':
                return f'{n:.1f} {u}' if u != 'B' else f'{n} B'
            n /= 1024

    with _log(c, 'export') as log, ThreadPoolExecutor(nproc) as ex:
        log.write(f'## export -> {target} dry_run={dry_run}\n')
        bad, moved_all, size_all = 0, 0, 0
        print(f'  {"item":32s} {"status":8s} {"to copy":>10s} {"total":>10s}')
        for name, rc, moved, size, err in ex.map(run, jobs):
            status = 'ok' if rc == 0 else f'rsync {rc}'           # rsync exit code: 0 = success
            print(f'  {name:32s} {status:8s} {human(moved):>10s} {human(size):>10s}' + (f'  {err}' if rc else ''))
            log.write(f'{name} rc={rc} copy={moved} total={size}\n')
            bad += rc != 0
            moved_all += moved
            size_all += size
    print(f'{"dry run: " if dry_run else ""}{len(jobs)} items -> {target}: {human(moved_all)} to copy '
          f'of {human(size_all)} in total; {bad} failed')
    return 1 if bad else 0
