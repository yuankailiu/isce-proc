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

from isce_proc.hpc.data import PKG, _log, _run

# interferogram steps whose rows are thinned by --select-pairs: step name -> config prefix
PAIR_STEPS = {'generate_burst_igram': 'config_generate_igram_', 'merge_burst_igram': 'config_merge_igram_',
              'filter_coherence': 'config_igram_filt_coh_', 'unwrap': 'config_igram_unw_'}


def prep(c, extra=()):
    """configs/ and run_files/ via run_isce_stack.py (DEM, orbits, stackSentinel.py)."""
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
           f"burstRampIon: {n['ramp']}/{len(ramp)} changed; keys {keys}")
    with _log(c, 'stack') as log:
        log.write(f'## ion-config: {msg}\n')
    print(msg)
    return 0


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
