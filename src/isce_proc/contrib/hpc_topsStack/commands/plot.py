"""`topsstack.py plot {ion,unw,baselines,network}`: quick-look figures into <stack>/pic/.

ion       ionosphere pair/date products (plot_imgs.py; was plot.sh)
unw       unwrapped interferograms (plot_imgs.py)
baselines perpendicular-baseline history (plot_baselines_isce.py)
network   interferogram network by starting range / IPF version (s1_network_check.py)
"""
import glob
import os
import sys

from commands.data import SCRIPTS, _log, _run

# plot_imgs.py calls: (input glob, --loc, --band, output name, extra args)
# units: ionospheric phase [rad]; azimuth shift [single-look azimuth lines, x ~14 m]; burst ramp [rad]
AMP = 'AMP'   # replaced by the first merged/interferograms/*/filt_fine.unw (amplitude background, same grid)
MARKS = ['pairs_diff_starting_ranges.txt:blue:diff. starting ranges', 'CHECK:orange:ionqc check',
         'EXCLUDE:red:excluded (ionqc / run_24)']   # CHECK/EXCLUDE: from the latest logs/ionqc_*.csv
IMGS = {
    'ion': [('ion/*_*/ion_cal/filt.ion', -3, 2, 'img_ion', ['--amp', '-u', 'rad'] + sum((['--mark', m] for m in MARKS), [])),
            ('ion_dates/*.ion', 1, 1, 'img_ion_dates', ['--wrap', '6.28', '-u', 'rad', '--amp-file', AMP, '--diff']),
            ('ion_azshift_dates/*.ion', 1, 1, 'img_azshiftDate', ['--wrap', '0.00628', '-u', 'az. lines', '--amp-file', AMP, '--diff']),
            ('ion_burst_ramp_merged_dates/*.float', -1, 1, 'img_ionRampDate', ['--wrap', '0.0628', '-u', 'rad', '--amp-file', AMP, '--diff'])],
    'unw': [('merged/interferograms/*_*/filt_fine.unw', -2, 2, 'img_unw', ['--amp'])],
}


def _ionqc_lists(c, tmp):
    """Pair lists from the latest ionqc CSV for --mark: {'CHECK': file, 'EXCLUDE': file}."""
    import csv
    f = sorted(glob.glob(os.path.join(c.stack, 'logs', 'ionqc_*.csv')))
    rows = list(csv.DictReader(open(f[-1]))) if f else []
    out = {}
    for key, test in (('CHECK', lambda s: s == 'check'), ('EXCLUDE', lambda s: s.startswith('exclude'))):
        out[key] = os.path.join(tmp, key.lower() + '.txt')
        open(out[key], 'w').write('\n'.join(r['pair'] for r in rows if test(r.get('status', ''))) + '\n')
    return out


def plot(c, what, extra=()):
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix='ionqc_marks_')
    try:
        return _plot(c, what, extra, _ionqc_lists(c, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _plot(c, what, extra, lists):
    pic = os.path.join(c.stack, 'pic')
    os.makedirs(pic, exist_ok=True)
    rc = 0
    with _log(c, 'plot') as log:
        if what in IMGS:
            for pat, loc, band, out, opts in IMGS[what]:
                if not glob.glob(os.path.join(c.stack, pat)):
                    print(f'skip {pat}: no files')
                    continue
                opts = [o.replace('CHECK:', lists['CHECK'] + ':').replace('EXCLUDE:', lists['EXCLUDE'] + ':')
                        for o in opts]                          # missing --mark lists are skipped by plot_imgs.py
                if AMP in opts:
                    unw = sorted(glob.glob(os.path.join(c.stack, 'merged', 'interferograms', '*_*', 'filt_fine.unw')))
                    i = opts.index(AMP)
                    if unw:
                        opts[i] = unw[0]
                    else:
                        del opts[i - 1:i + 1]
                rc |= _run([sys.executable, os.path.join(SCRIPTS, 'plot_imgs.py'), '-i', pat, '--redo', '--loc', str(loc),
                            '--band', str(band), '--out', os.path.join('pic', out), *opts, *extra], c.stack, log)
        elif what == 'baselines':
            rc = _run([sys.executable, os.path.join(SCRIPTS, 'plot_baselines_isce.py'), '--dir', 'baselines',
                       '--out', 'pic', '--name', c.hpc.track, *extra], c.stack, log)
        elif what == 'network':
            version = os.path.join(c.data, 's1_version.txt')
            if not os.path.isfile(version):
                sys.exit(f'{version} not found: run `topsstack.py inspect` first')
            for step, name in (('unwrap', 'unw'), ('unwrap_ion', 'ion')):
                run = sorted(glob.glob(os.path.join(c.stack, 'run_files', f'run_[0-9][0-9]_{step}')))
                if run:
                    rc |= _run([sys.executable, os.path.join(SCRIPTS, 's1_network_check.py'), '-v', version,
                                '-l', run[0], '-n', name, *extra], pic, log)
        else:
            sys.exit(f'unknown plot target {what!r}; choose from ion, unw, baselines, network')
    return rc
