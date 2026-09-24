#!/usr/bin/env python3
"""Delete large intermediate files of an ISCE2 topsStack (stackSentinel.py) run.

Each target has a "kill-after" step: the last step that reads those files. The step
depends on how the stack was made, so it is resolved from the stack itself:
  zips read via VRT : unpacked bursts in reference/ and secondarys/ are VRTs into SLC/*.zip
                      (topsStack always unpacks virtually, except IPF 2.36 aux corrections)
  virtual_merge     : merged/SLC and merged/geom_reference are VRTs (-V True) or real files (-V False)
  ionosphere        : run_files contain the ionosphere steps (subband_and_resamp, ...)

Run from the stack directory (the one holding run_files/) or from run_files/.
  clean_topsStack.py                        # show the resolved table for this stack
  clean_topsStack.py coarse_igram burst_igram       # dry run: count files and sizes
  clean_topsStack.py coarse_igram burst_igram --delete
  clean_topsStack.py coarse_igram burst_igram --delete --reuse   # use the dry-run list, no new search
With --delete, each target is deleted only if its kill-after step has all its outputs
(checked row by row with check_topsStack.py; Slurm COMPLETED alone is not proof).
"""
import argparse, fnmatch, glob, os, re, stat, sys
from pathlib import PurePosixPath
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


def stack_info(root):
    steps = {}
    for f in os.listdir(os.path.join(root, 'run_files')):
        m = re.fullmatch(r'run_(\d+)_(\w+)', f)
        if m:
            steps[m.group(2)] = int(m.group(1))
    vrts = glob.glob(os.path.join(root, 'reference/IW*/burst_01.slc.vrt'))[:1] \
         + glob.glob(os.path.join(root, 'secondarys/*/IW*/burst_01.slc.vrt'))[:3]
    zip_vrt = any('/vsizip/' in open(v).read() for v in vrts) if vrts else True   # not unpacked yet

    # virtual_merge as written by stackSentinel.py into the merge config (unknown -> True, keeps more)
    virtual = True
    if 'merge_reference_secondary_slc' in steps:
        run = os.path.join(root, 'run_files', f'run_{steps["merge_reference_secondary_slc"]:02d}_merge_reference_secondary_slc')
        cfg = open(run).readline().split()[-1]
        m = re.search(r'use_virtual_files\s*:\s*(\w+)', open(cfg).read()) if os.path.isfile(cfg) else None
        virtual = m is None or m.group(1) == 'True'
    return steps, zip_vrt, virtual, 'subband_and_resamp' in steps


def rules(zip_vrt, virtual, ion):
    """target: (glob patterns relative to stack root, kill-after step name or None = keep, note)"""
    last_zip_reader = ('subband_and_resamp' if ion else
                       'filter_coherence' if virtual else 'generate_burst_igram')
    return {
        'safe_zip':        (['SLC/*.zip'],
                            last_zip_reader if zip_vrt else 'unpack_secondary_slc',
                            'raw SLC zips; read via reference/ and secondarys/ VRTs'),
        'coreg_overlap':   (['coreg_secondarys/*/overlap/IW*/*.slc', 'coreg_secondarys/*/overlap/IW*/*.off'],
                            'timeseries_misreg', 'overlap SLCs and offsets (ESD)'),
        'esd':             ([f'ESD/*/IW*/*.{e}' for e in ('int', 'bin', 'cor', 'off')],
                            'timeseries_misreg', 'ESD files'),
        'coarse_igram':    (['coarse_interferograms/*/overlap/IW*/int_*.int'],
                            'timeseries_misreg', 'coarse overlap interferograms (ESD)'),
        'geom_reference':  (['geom_reference/IW*/*.rdr'],
                            None if virtual else 'merge_reference_secondary_slc',
                            'burst geometry; merged/geom_reference VRTs read it if virtual_merge'),
        'burst_slc':       (['coreg_secondarys/*/IW*/burst_[0-9][0-9].slc'],
                            'filter_coherence' if virtual else 'generate_burst_igram',
                            'coregistered full-band burst SLCs'),
        'coreg_offset':    (['coreg_secondarys/*/IW*/range_[0-9][0-9].off', 'coreg_secondarys/*/IW*/azimuth_[0-9][0-9].off'],
                            'subband_and_resamp' if ion else 'fullBurst_resample',
                            'range/azimuth offsets for resampling'),
        'burst_igram':     (['interferograms/*/IW*/fine_[0-9][0-9].int'],
                            'merge_burst_igram', 'burst interferograms; removes full-resolution ifgs'),
        'merged_slc':      (['merged/SLC/*/*.slc.full'],
                            'filter_coherence', 'merged SLCs (real files only if not virtual_merge)'),
        'ion_burst_slc':   (['reference/IW*/burst_[0-9][0-9]_lower.slc', 'reference/IW*/burst_[0-9][0-9]_upper.slc',
                             'coreg_secondarys/*/IW*/burst_[0-9][0-9]_lower.slc', 'coreg_secondarys/*/IW*/burst_[0-9][0-9]_upper.slc'],
                            'generateIgram_ion', 'sub-band burst SLCs'),
        'ion_burst_igram': (['ion/*/lower/interferograms/IW*/fine_*.int', 'ion/*/upper/interferograms/IW*/fine_*.int'],
                            'mergeBurstsIon', 'sub-band burst interferograms'),
        'ion_split_igram': ([f'ion/*/{b}/merged/*.{e}' for b in ('lower', 'upper') for e in ('int', 'cor', 'unw', 'conncomp')],
                            'computeIon', 'merged sub-band interferograms'),
        'ion_burst_ramp':  (['ion_burst_ramp_dates/*/IW*/burst*.float'],
                            'mergeBurstRampIon', 'burst-level ionosphere ramps'),
    }


def find(root, patterns, nproc):
    """{path: size} of regular files matching the patterns.

    Each matching directory is listed once (os.scandir) for all file-name patterns
    that share it, and the size comes from the same directory entry: one stat per file.
    """
    names = {}                                   # directory glob -> file-name patterns
    for p in patterns:
        d, n = os.path.split(p)
        names.setdefault(d, []).append(n)

    def dirs(dglob):                             # expand the directory glob, parallel below its first wildcard
        parts = dglob.split('/')
        i = next((i for i, c in enumerate(parts) if any(ch in c for ch in '*?[')), None)
        if i is None:
            return [os.path.join(root, dglob)]
        top = os.path.join(root, *parts[:i])
        tops = [e.path for e in os.scandir(top) if e.is_dir() and fnmatch.fnmatchcase(e.name, parts[i])] \
               if os.path.isdir(top) else []
        if i == len(parts) - 1:
            return tops
        with ThreadPoolExecutor(nproc) as ex:
            return [d for ds in ex.map(lambda t: glob.glob(os.path.join(t, *parts[i + 1:])), tops) for d in ds]

    def scan(d, pats):
        try:
            with os.scandir(d) as it:
                return [(e.path, e.stat(follow_symlinks=False).st_size) for e in it
                        if any(fnmatch.fnmatchcase(e.name, n) for n in pats) and e.is_file(follow_symlinks=False)]
        except (FileNotFoundError, NotADirectoryError):
            return []

    out = {}
    with ThreadPoolExecutor(nproc) as ex:
        for dglob, pats in names.items():
            for fs in ex.map(lambda d: scan(d, pats), dirs(dglob)):
                out.update(fs)
    return out


def from_log(logs, target, root, patterns, nproc):
    """{path: size} from the latest dry-run list of `target` in the logs, re-checked against its patterns."""
    files, cur = None, False
    for log in logs:                             # oldest first; the last matching block wins
        for line in open(log):
            if line.startswith('## '):
                w = line.split()
                cur = w[3] == target and 'delete=False' in w
                if cur:
                    files = []
            elif cur:
                files.append(line.rstrip('\n'))
    if files is None:
        return None
    parts = [PurePosixPath(p).parts for p in patterns]
    def match(f):
        rel = PurePosixPath(os.path.relpath(f, root))
        return any(len(rel.parts) == len(q) and rel.match(str(PurePosixPath(*q))) for q in parts)
    def size(f):
        try:
            st = os.lstat(f)
            return st.st_size if stat.S_ISREG(st.st_mode) else None
        except FileNotFoundError:
            return None
    files = [f for f in files if match(f)]
    with ThreadPoolExecutor(nproc) as ex:
        return {f: s for f, s in zip(files, ex.map(size, files)) if s is not None}


def unfinished(root, steps, kill, nproc):
    """'' if the kill-after step has all its outputs (check_topsStack.py), else a reason."""
    if kill not in steps:
        return f'{kill} not in run_files'
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import check_topsStack as chk
    run = f'run_{steps[kill]:02d}_{kill}'
    old = os.getcwd()
    os.chdir(os.path.join(root, 'run_files'))
    try:
        _, bad, _ = chk.evaluate(run, SimpleNamespace(nproc=nproc, cleaned_ok=False, verbose=False))
    finally:
        os.chdir(old)
    return f'{len(bad)} rows of {run} lack their outputs' if bad else ''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('targets', nargs='*', help='targets to delete (see table), or "all"')
    ap.add_argument('--delete', action='store_true', help='actually delete (default: dry run)')
    ap.add_argument('--reuse', action='store_true',
                    help='with --delete: delete the file list of the latest dry run of each target '
                         '(re-checked against its patterns; no new search)')
    ap.add_argument('--no-check', action='store_true',
                    help='with --delete: skip checking that the kill-after step produced all its outputs')
    ap.add_argument('-n', '--nproc', type=int, default=32, help='parallel threads (default: %(default)s)')
    args = ap.parse_args()

    root = os.getcwd()
    if not os.path.isdir(os.path.join(root, 'run_files')):
        root = os.path.dirname(root)
    if not os.path.isdir(os.path.join(root, 'run_files')):
        sys.exit('run from the stack directory or its run_files/')

    steps, zip_vrt, virtual, ion = stack_info(root)
    table = rules(zip_vrt, virtual, ion)
    kill = lambda k: 'keep' if k is None else (f'run_{steps[k]:02d}_{k}' if k in steps else f'{k} (not in run_files)')
    print(f'stack: {root}\n  bursts read from zips: {zip_vrt}   virtual_merge: {virtual}   ionosphere: {ion}\n')
    print(f'  {"target":16s} {"delete after":36s} note')
    for t, (_, k, note) in table.items():
        print(f'  {t:16s} {kill(k):36s} {note}')
    if not args.targets:
        return

    targets = list(table) if 'all' in args.targets else args.targets
    bad = [t for t in targets if t not in table]
    if bad:
        sys.exit(f'unknown target(s): {bad}')
    log = os.path.join(root, 'run_files', f'clean_topsStack_{datetime.now():%Y-%m-%d}.log')
    logs = sorted(glob.glob(os.path.join(root, 'run_files', 'clean_topsStack_*.log')))
    total = 0
    print(f'\n{"DELETING" if args.delete else "dry run"}; file lists -> {log}')
    with open(log, 'a') as fl, ThreadPoolExecutor(args.nproc) as ex:
        for t in targets:
            pats, k, _ = table[t]
            if k is None:
                print(f'  {t:16s} skipped: needed for the lifetime of this stack'); continue
            if args.delete and not args.no_check:
                reason = unfinished(root, steps, k, args.nproc)
                if reason:
                    print(f'  {t:16s} NOT deleted: {reason} (use --no-check to override)'); continue
            files = from_log(logs, t, root, pats, args.nproc) if args.reuse else None
            if args.reuse and files is None:
                print(f'  {t:16s} no dry-run list in {os.path.basename(log)} etc.; searching')
            reused = files is not None
            if not reused:
                files = find(root, pats, args.nproc)
            size = sum(files.values())
            fl.write(f'## {datetime.now():%F %T} {t} delete={args.delete} after={kill(k)} '
                     f'files={len(files)} bytes={size}\n' + ''.join(f + '\n' for f in sorted(files)))
            fl.flush()
            if args.delete:
                list(ex.map(os.remove, files))
            total += size
            print(f'  {t:16s} {len(files):9d} files  {size/1e12:8.3f} TB' + ('  (from dry-run list)' if reused else ''))
    print(f'  {"total":16s} {"":15s}  {total/1e12:8.3f} TB {"deleted" if args.delete else "would be deleted"}')


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    main()
