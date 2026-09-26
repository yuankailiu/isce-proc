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
    ('merged/interferograms', []),
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


def export(c, dry_run=False, nproc=4):
    target = c.hpc.exportDir
    if not target:
        sys.exit('set hpc.exportDir in the template (target directory for the copy)')
    if not dry_run:
        os.makedirs(target, exist_ok=True)
    jobs = []
    for pattern, opts in ITEMS:
        srcs = sorted(glob.glob(os.path.join(c.stack, pattern)))
        if srcs:
            rel = [os.path.relpath(s, c.stack) for s in srcs]
            jobs.append((pattern, ['rsync', '-aR', *(['-n'] if dry_run else []), '--stats', *opts, *rel, target + '/']))
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
        moved = next((l.split(':', 1)[1].strip() for l in r.stdout.splitlines()
                      if l.startswith('Total transferred file size')), '?')
        return name, r.returncode, moved, r.stderr.strip()[-300:]

    with _log(c, 'export') as log, ThreadPoolExecutor(nproc) as ex:
        log.write(f'## export -> {target} dry_run={dry_run}\n')
        bad = 0
        for name, rc, moved, err in ex.map(run, jobs):
            print(f'  {name:32s} rc={rc} transferred {moved}' + (f'  {err}' if rc else ''))
            log.write(f'{name} rc={rc} {moved}\n')
            bad += rc != 0
    print(f'{"dry run: " if dry_run else ""}{len(jobs)} items -> {target}; {bad} failed')
    return 1 if bad else 0
