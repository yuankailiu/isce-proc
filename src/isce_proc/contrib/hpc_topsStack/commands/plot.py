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
IMGS = {
    'ion': [('ion/*_*/ion_cal/filt.ion', -3, 2, 'img_ion', ['--txt', 'pairs_diff_starting_ranges.txt', '--amp']),
            ('ion_dates/*.ion', 1, 1, 'img_ion_dates', ['--wrap', '6.28']),
            ('ion_azshift_dates/*.ion', 1, 1, 'img_azshiftDate', ['--wrap', '0.00628']),
            ('ion_burst_ramp_merged_dates/*.float', -1, 1, 'img_ionRampDate', ['--wrap', '0.0628'])],
    'unw': [('merged/interferograms/*_*/filt_fine.unw', -2, 2, 'img_unw', ['--amp'])],
}


def plot(c, what, extra=()):
    pic = os.path.join(c.stack, 'pic')
    os.makedirs(pic, exist_ok=True)
    rc = 0
    with _log(c, 'plot') as log:
        if what in IMGS:
            for pat, loc, band, out, opts in IMGS[what]:
                if not glob.glob(os.path.join(c.stack, pat)):
                    print(f'skip {pat}: no files')
                    continue
                opts = list(opts)
                if '--txt' in opts and not os.path.isfile(os.path.join(c.stack, opts[opts.index('--txt') + 1])):
                    i = opts.index('--txt')
                    del opts[i:i + 2]                           # highlight list not there: plot without it
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
