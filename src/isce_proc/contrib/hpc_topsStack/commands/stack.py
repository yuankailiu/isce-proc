"""Stack side of topsstack.py: run files (run_isce_stack.py), pair selection, ionosphere config edits.

All edits are idempotent: pair selection always starts from the original run files saved in
run_files/preselect/, and config keys are set (replaced if present), never appended twice.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

from commands.data import PKG, _log, _run

# interferogram steps whose rows are thinned by --select-pairs: step name -> config prefix
PAIR_STEPS = {'generate_burst_igram': 'config_generate_igram_', 'merge_burst_igram': 'config_merge_igram_',
              'filter_coherence': 'config_igram_filt_coh_', 'unwrap': 'config_igram_unw_'}


def prep(c, extra=()):
    """configs/ and run_files/ via run_isce_stack.py (DEM, orbits, stackSentinel.py)."""
    slc = os.path.join(c.stack, 'SLC')                          # stackSentinel.py reads <stack>/SLC
    if not os.path.lexists(slc):
        os.symlink(os.path.relpath(c.data, c.stack), slc)
        print(f'SLC -> {os.path.relpath(c.data, c.stack)} (asf.dataDir)')
    elif os.path.realpath(slc) != os.path.realpath(c.data):
        print(f'warning: SLC is {os.path.realpath(slc)}, not asf.dataDir {c.data}')
    with _log(c, 'stack') as log:
        return _run([sys.executable, os.path.join(PKG, 'run_isce_stack.py'), c.template, *extra], c.stack, log)


# ---------------------------------------------------------------- config edits
def set_keys(cfg, keys):
    """Set `key : value` lines in a SentinelWrapper config; replace if present, else append. Returns changed."""
    s = open(cfg).read()
    new = s
    for k, v in keys.items():
        line = f'{k} : {v}'
        pat = re.compile(rf'^[ \t]*{re.escape(k)}[ \t]*:.*$', re.M)
        new = pat.sub(line, new, count=1) if pat.search(new) else new.rstrip('\n') + f'\n{line}\n'
    if new != s:
        with open(cfg, 'w') as f:
            f.write(new)
    return new != s


def ion_config(c):
    """filtIon: wbdfile, iteration, fill, maskfile, swath_align (pairs with different starting ranges);
    burstRampIon: maskfile. Replaces filtIon_config.sh."""
    configs = os.path.join(c.stack, 'configs')
    filt = sorted(glob.glob(os.path.join(configs, 'config_filtIon_*')))
    ramp = sorted(glob.glob(os.path.join(configs, 'config_burstRampIon_*')))
    if not filt and not ramp:
        sys.exit(f'no config_filtIon_* / config_burstRampIon_* in {configs}: ionosphere steps not set up')
    keys = {'iteration': c.ion.iteration, 'fill': c.ion.fill}
    if c.ion.wbdFile:
        keys['wbdfile'] = c.ion.wbdFile
    else:
        print('WARNING: no ion.wbdFile (and none found next to isce.demFile): wbdfile not set')
    if c.ion.maskFile:
        keys['maskfile'] = c.ion.maskFile
    diff = set()
    f = os.path.join(c.stack, 'pairs_diff_starting_ranges.txt')
    if c.ion.swathAlign and os.path.isfile(f):
        diff = {x for x in open(f).read().split() if re.fullmatch(r'\d{8}_\d{8}', x)}
    n = {'filt': 0, 'align': 0, 'ramp': 0}
    for cfg in filt:
        pair = os.path.basename(cfg)[len('config_filtIon_'):]
        k = dict(keys, **({'swath_align': True} if pair in diff else {}))
        n['filt'] += set_keys(cfg, k)
        n['align'] += pair in diff
    for cfg in ramp:
        n['ramp'] += set_keys(cfg, {'maskfile': c.ion.burstRampMask})
    msg = (f"filtIon: {n['filt']}/{len(filt)} configs changed ({n['align']} with swath_align); "
           f"burstRampIon: {n['ramp']}/{len(ramp)} changed; keys {keys}; {merge_swath_last(c)}")
    with _log(c, 'stack') as log:
        log.write(f'## ion-config: {msg}\n')
    print(msg)
    return 0


def merge_swath_last(c):
    """Put the mergeSwathIon.py rows of run_NN_computeIon after all computeIon.py rows (stable).
    Each merge needs the sub-swath results of its pair; `topsstack.py jobs` then gives them their
    own job part, which runs after the computeIon part."""
    runs = glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_computeIon'))
    if not runs:
        return 'no computeIon run file'
    rows = [l for l in open(runs[0]).read().splitlines() if l.strip()]
    merge = [l for l in rows if l.split()[0] == 'mergeSwathIon.py']
    new = [l for l in rows if l.split()[0] != 'mergeSwathIon.py'] + merge
    if new != rows:
        with open(runs[0], 'w') as f:
            f.write(''.join(l + '\n' for l in new))
    return (f'{os.path.basename(runs[0])}: {len(merge)} mergeSwathIon rows '
            f'{"moved to the end" if new != rows else "already last"} (re-run `topsstack.py jobs`)')


# ---------------------------------------------------------------- pair selection
def _dates(c):
    """Acquisition dates from SAFE_files.txt (start time of each SAFE name)."""
    dates = set()
    for line in open(os.path.join(c.stack, 'SAFE_files.txt')).read().split():
        m = re.search(r'_(\d{8})T\d{6}_\d{8}T\d{6}_', os.path.basename(line))
        if m:
            dates.add(m.group(1))
    return sorted(dates)


def select_pairs(c, num=None, bridge=None):
    """Keep, per date, the `num` nearest following dates plus the one `bridge` dates ahead
    (interferogram steps only; ionosphere pairs untouched). Original run files -> run_files/preselect/."""
    num = num or c.select.numConnections
    bridge = bridge or c.select.bridge or num
    if not num:
        sys.exit('need select.numConnections (or --select-pairs N [BRIDGE])')
    dates = _dates(c)
    keep = {f'{dates[i]}_{dates[j]}' for i in range(len(dates))
            for j in list(range(i + 1, i + 1 + num)) + [i + bridge] if j < len(dates)}
    keep |= _extra_pairs(c)                                      # added with --add-pairs: always kept
    run_dir = os.path.join(c.stack, 'run_files')
    pre = os.path.join(run_dir, 'preselect')
    os.makedirs(pre, exist_ok=True)
    lines_out = []
    for f in sorted(glob.glob(os.path.join(run_dir, 'run_[0-9][0-9]_*'))):
        name = os.path.basename(f)
        step = re.sub(r'^run_\d+_', '', name)
        if '.' in name or step not in PAIR_STEPS:
            continue
        bak = os.path.join(pre, name)
        if not os.path.exists(bak):
            shutil.copyfile(f, bak)                              # the original, kept once
        rows = [l for l in open(bak).read().splitlines() if l.strip()]
        sel = [l for l in rows if os.path.basename(l.split()[-1])[len(PAIR_STEPS[step]):] in keep]
        with open(f, 'w') as out:
            out.write(''.join(l + '\n' for l in sel))
        lines_out.append(f'{name}: {len(sel)}/{len(rows)} rows')
    msg = f'{len(dates)} dates, {len(keep)} pairs (nearest {num} + bridge {bridge}); ' + '; '.join(lines_out)
    with _log(c, 'stack') as log:
        log.write(f'## select-pairs: {msg}\n')
    print(msg + '\nre-run `topsstack.py jobs` so the job arrays match the new row counts')
    return 0


# ---------------------------------------------------------------- extra pairs (e.g. same-season bridges)
def _extra_pairs(c):
    f = os.path.join(c.stack, 'run_files', 'extra_pairs.txt')
    return set(open(f).read().split()) if os.path.isfile(f) else set()


def add_pairs(c, pair_file):
    """Add interferogram pairs that stackSentinel.py did not make (e.g. same-season pairs across a data gap).

    For each pair D1_D2 (D1 < D2, both coregistered, neither the stack reference date) the four configs of
    steps 13-16 are copied from an existing pair of the same kind with the dates replaced, and the rows are
    appended to the run files (and to run_files/preselect/, so --select-pairs keeps them). The pairs are
    recorded in run_files/extra_pairs.txt. Idempotent: pairs already present are skipped. Both dates must be in
    the stack (SAFE_files.txt); new dates may still be unprocessed (steps 1-12 make them before the pair steps).
    Needs, for old dates: coreg_secondarys/<date>/IW*/burst_*.slc (generate_burst_igram) and merged/SLC/<date>
    (filter_coherence), i.e. do not clean burst_slc / merged_slc before these pairs ran."""
    run_dir = os.path.join(c.stack, 'run_files')
    want = sorted({p for p in open(pair_file).read().split() if re.fullmatch(r'\d{8}_\d{8}', p)})
    cfg = os.path.join(c.stack, 'configs')
    rows = {step: open(os.path.join(run_dir, f)).read() for f in os.listdir(run_dir)
            for step in [re.sub(r'^run_\d+_', '', f)] if re.fullmatch(r'run_\d+_\w+', f) and step in PAIR_STEPS}
    # template: an existing pair whose first date is a coregistered secondary (not the stack reference)
    tmpl = next(os.path.basename(f)[len('config_generate_igram_'):] for f in sorted(glob.glob(os.path.join(cfg, 'config_generate_igram_*')))
                if 'coreg_secondarys' in open(f).read().split('reference :')[1].split('\n')[0])
    t1, t2 = tmpl.split('_')
    stack_dates = set(_dates(c))                                 # dates of this stack (coregistered or to be)
    ref_date = min(stack_dates)
    added, missing = [], []
    for p in want:
        d1, d2 = p.split('_')
        if not {d1, d2} <= stack_dates or ref_date in (d1, d2):
            missing.append(p)                                    # not in the stack (or the reference: other template)
            continue
        if all(f'{PAIR_STEPS[s]}{p}' in txt for s, txt in rows.items()):
            continue
        for step, prefix in PAIR_STEPS.items():
            src = os.path.join(cfg, f'{prefix}{tmpl}')
            dst = os.path.join(cfg, f'{prefix}{p}')
            text = open(src).read().replace(f'{t1}_{t2}', p).replace(f'/{t1}', f'/{d1}').replace(f'/{t2}', f'/{d2}')
            text = text.replace(f'{t1}.slc', f'{d1}.slc').replace(f'{t2}.slc', f'{d2}.slc')
            open(dst, 'w').write(text)
            line = f'SentinelWrapper.py -c {dst}\n'
            for f in [f for f in os.listdir(run_dir) if re.fullmatch(rf'run_\d+_{step}', f)]:
                for path in (os.path.join(run_dir, f), os.path.join(run_dir, 'preselect', f)):
                    if os.path.isfile(path) and dst not in open(path).read():
                        with open(path, 'a') as out:
                            out.write(line)
        added.append(p)
    with open(os.path.join(run_dir, 'extra_pairs.txt'), 'a') as f:
        f.write(''.join(p + '\n' for p in added))
    msg = f'add-pairs: {len(added)} added, {len(want) - len(added) - len(missing)} already present, {len(missing)} skipped ' \
          f'(a date not in this stack, or the reference: {missing[:6]}{"..." if len(missing) > 6 else ""}); template {tmpl}'
    with _log(c, 'stack') as log:
        log.write(f'## {msg}\n')
    print(msg + '\nre-run `topsstack.py jobs` so the job arrays match the new row counts')
    return 1 if missing else 0
