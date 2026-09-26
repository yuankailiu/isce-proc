"""`topsstack.py ionqc`: flag bad ionosphere pairs after filtIon (step 23), before invertIon (step 24).

Two tests per pair, both on the filtered ionospheric phase ion/<pair>/ion_cal/filt.ion (rad):

1. Loop closure. For every triangle of ion pairs (a,b), (b,c), (a,c):
       e = I(a,b) + I(b,c) - I(a,c)            (0 for consistent pairs; a constant is removed)
   A pair's closure score is the median RMS of e over the triangles it is in. One bad pair makes
   all its triangles large, while each good pair also sits in triangles without it, so the median
   isolates the bad one.
2. Correction test (--unw). For pairs that also have an interferogram, the semivariogram of the
   unwrapped phase, gamma(r) = 0.5 E[(phi(x+r) - phi(x))^2], at r = 10/30/50 km, before and after
   subtracting the ionosphere. A ratio after/before > 1 means the correction adds variance.

Pairs are suggested for exclusion (invertIon.py --exc_pair) only if the network stays connected
without them. --apply writes the list into run_24_invertIon and run_26_invertIonShift (replaces an
earlier --exc_pair there). Results: logs/ionqc_<date>.txt and .csv.
"""
import csv
import glob
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import combinations

import numpy as np

from commands.data import _log

PAIR = re.compile(r'^\d{8}_\d{8}$')


def _size(xml):
    s = open(xml).read()
    return (int(re.search(r'name="width">\s*<value>(\d+)', s).group(1)),
            int(re.search(r'name="length">\s*<value>(\d+)', s).group(1)))


def _band2(f, step=1):
    """Second band of a 2-band BIL float32 ISCE file (ion or unw phase); 0 -> NaN."""
    w, l = _size(f + '.xml')
    a = np.fromfile(f, dtype=np.float32).reshape(2 * l, w)[1::2][::step, ::step]
    return np.where(a == 0, np.nan, a)


def _connected(pairs):
    par = {}

    def root(a):
        par.setdefault(a, a)
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for p in pairs:
        a, b = p.split('_')
        par[root(a)] = root(b)
    return len({root(d) for p in pairs for d in p.split('_')}) == 1


# ---------------------------------------------------------------- 1. loop closure
def closure(ion, nproc):
    """Closure RMS of every triangle of ion pairs; returns the triangles and their RMS."""
    pairs = set(ion)
    by_ref = {}
    for p in pairs:
        a, b = p.split('_')
        by_ref.setdefault(a, set()).add(b)
    tris = [(f'{a}_{b}', f'{b}_{c}', f'{a}_{c}')
            for a, bs in by_ref.items() for b, c in combinations(sorted(bs), 2)
            if f'{b}_{c}' in pairs]

    def rms(t):
        e = ion[t[0]] + ion[t[1]] - ion[t[2]]
        e = e[np.isfinite(e)]
        return np.sqrt(np.mean((e - np.median(e)) ** 2)) if e.size > 100 else np.nan
    with ThreadPoolExecutor(nproc) as ex:
        r = list(ex.map(rms, tris))
    return [(t, v) for t, v in zip(tris, r) if np.isfinite(v)]


def attribute(pairs, tri_rms, floor, nmad):
    """Per-pair closure score = median RMS of its triangles, attributed greedily.

    With numConnectionIon = 3 a pair sits in only 2-4 triangles, so one bad pair lifts the median
    of its partners too. Flag the worst pair above the threshold, ignore the triangles it is in when
    scoring the other pairs, rescore, and repeat.
    """
    def scores(flagged):
        per = {p: [] for p in pairs}
        for t, v in tri_rms:
            for p in t:
                if not any(q in flagged for q in t if q != p):
                    per[p].append(v)
        return {p: (float(np.median(v)) if v else np.nan, len(v)) for p, v in per.items()}
    s0 = scores(set())
    v = np.array([x for x, _ in s0.values() if np.isfinite(x)])
    med, mad = np.median(v), 1.4826 * np.median(np.abs(v - np.median(v)))
    thr = max(floor, med + nmad * mad)
    flagged = []
    while True:
        s = scores(set(flagged))
        cand = [(x, p) for p, (x, _) in s.items() if p not in flagged and np.isfinite(x) and x > thr]
        if not cand:
            return s, flagged, (med, mad, thr)
        flagged.append(max(cand)[1])


# ---------------------------------------------------------------- 2. correction test
def _semivar(phi, lags):
    """0.5 mean squared difference for pixel offsets `lags` (list of (dy, dx)); NaN-aware."""
    out = []
    for dy, dx in lags:
        a = phi[max(dy, 0):phi.shape[0] + min(dy, 0), max(dx, 0):phi.shape[1] + min(dx, 0)]
        b = phi[max(-dy, 0):phi.shape[0] + min(-dy, 0), max(-dx, 0):phi.shape[1] + min(-dx, 0)]
        d = (a - b)[np.isfinite(a - b)]
        out.append(0.5 * np.mean(d ** 2) if d.size > 100 else np.nan)
    return np.nanmean(out)


def correction(c, pairs, shape, dists, nproc, step=1):
    """Semivariogram ratio after/before ion removal at `dists` km, for pairs with an interferogram.

    Convention: corrected = unw - ion (the ionospheric phase of the pair is subtracted; Liang et
    al., 2019). The first pairs are also tested with + ion, as a check of that sign.
    """
    lat = _geom(c, 'lat', shape)
    lon = _geom(c, 'lon', shape)
    dy_km = np.nanmedian(np.abs(np.diff(lat, axis=0))) * 111.2
    dx_km = np.hypot(np.nanmedian(np.abs(np.diff(lat, axis=1))) * 111.2,
                     np.nanmedian(np.abs(np.diff(lon, axis=1))) * 111.2 * np.cos(np.radians(np.nanmean(lat))))
    lags = {r: [(int(round(r * np.sin(t) / dy_km)), int(round(r * np.cos(t) / dx_km)))
                for t in np.linspace(0, np.pi, 8, endpoint=False)] for r in dists}
    ifg_dir = os.path.join(c.stack, 'merged', 'interferograms')
    have = [p for p in pairs if os.path.isfile(os.path.join(ifg_dir, p, 'filt_fine.unw'))]
    check = set(have[::max(1, len(have) // 20)][:20])          # ~20 pairs spread in time: sign check

    def one(p):
        from skimage.transform import resize
        unw = resize(_band2(os.path.join(ifg_dir, p, 'filt_fine.unw')), shape, order=0,
                     preserve_range=True, anti_aliasing=False)         # to the ion grid (nearest)
        ion = _band2(os.path.join(c.stack, 'ion', p, 'ion_cal', 'filt.ion'))
        g0 = [_semivar(unw, lags[r]) for r in dists]
        out = {'minus': [_semivar(unw - ion, lags[r]) / g for r, g in zip(dists, g0)]}
        if p in check:
            out['plus'] = [_semivar(unw + ion, lags[r]) / g for r, g in zip(dists, g0)]
        return p, out
    with ThreadPoolExecutor(nproc) as ex:
        out = dict(ex.map(one, have))
    chk = [(v['minus'][-1], v['plus'][-1]) for v in out.values() if 'plus' in v]
    sign_ok = sum(m < pl for m, pl in chk)
    return {p: v['minus'] for p, v in out.items()}, (sign_ok, len(chk)), (dy_km, dx_km)


def _geom(c, name, shape):
    from skimage.transform import resize
    f = os.path.join(c.stack, 'merged', 'geom_reference', f'{name}.rdr')
    w, l = _size(f + '.xml')
    a = np.fromfile(f, dtype=np.float64).reshape(l, w)
    return resize(a, shape, order=1, preserve_range=True, anti_aliasing=False)


# ---------------------------------------------------------------- driver
def _exc_now(c):
    run = sorted(glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_invertIon')))
    if not run:
        return [], None
    m = re.search(r'--exc_pair((?:\s+\d{8}_\d{8})+)', open(run[0]).read())
    return (m.group(1).split() if m else []), run[0]


def apply_exc(c, exc):
    """Set --exc_pair in run_NN_invertIon and run_NN_invertIonShift (replace, not append)."""
    done = []
    for f in sorted(glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_invertIon*'))):
        if not re.fullmatch(r'run_\d\d_invertIon(Shift)?', os.path.basename(f)):
            continue
        s = open(f).read().rstrip('\n')
        s = re.sub(r'\s+--exc_pair(\s+\d{8}_\d{8})+', '', s)
        if exc:
            s += ' --exc_pair ' + ' '.join(sorted(exc))
        open(f, 'w').write(s + '\n')
        done.append(os.path.basename(f))
    return done


def ionqc(c, unw=False, apply=False, nproc=8, step=2, floor=3.0, nmad=6.0, ratio_max=1.2,
          dists=(10, 30, 50), sample=0):
    files = sorted(glob.glob(os.path.join(c.stack, 'ion', '*_*', 'ion_cal', 'filt.ion')))
    pairs = [f.split(os.sep)[-3] for f in files if PAIR.match(f.split(os.sep)[-3])]
    if not pairs:
        raise SystemExit('no ion/*_*/ion_cal/filt.ion: run steps 17-23 first')
    print(f'reading {len(pairs)} filt.ion (every {step}. pixel) ...', flush=True)
    with ThreadPoolExecutor(nproc) as ex:
        ion = dict(zip(pairs, ex.map(lambda p: _band2(os.path.join(c.stack, 'ion', p, 'ion_cal', 'filt.ion'), step), pairs)))

    tri_rms = closure(ion, nproc)
    ntri = len(tri_rms)
    clo, flagged_clo, (med, mad, thr) = attribute(pairs, tri_rms, floor, nmad)
    print(f'{ntri} triangles; closure RMS per pair: median {med:.2f} rad, MAD {mad:.2f}; flag > {thr:.2f} rad')

    cr, sign, sp = ({}, None, None)
    if unw:
        shape = _band2(os.path.join(c.stack, 'ion', pairs[0], 'ion_cal', 'filt.ion')).shape
        print('correction test on interferograms ...', flush=True)
        flagged = flagged_clo
        rng = np.random.default_rng(0)
        sub = pairs if not sample or sample >= len(pairs) else sorted(set(flagged) | set(rng.choice(pairs, sample, replace=False)))
        print(f'correction test on the interferograms of {len(sub)} pairs '
              f'({"all" if sub is pairs else f"{len(flagged)} flagged + {sample} random"}) ...', flush=True)
        cr, sign, sp = correction(c, sub, shape, dists, nproc)
        print(f'{len(cr)} pairs with an interferogram; pixel {sp[0]:.2f} x {sp[1]:.2f} km (az x rg); '
              f'sign check: unw - ion reduces the variance more than unw + ion in {sign[0]}/{sign[1]} pairs')

    # decision: closure says the pair is inconsistent with the network; the correction test says
    # whether it still helps this interferogram. Exclude when closure flags it and the correction
    # does not reduce the variance (or there is no interferogram to test); the rest is listed to check.
    rows, bad, check = [], [], []
    for p in sorted(pairs):
        s_, n = clo[p]
        r = cr.get(p)
        r50 = r[-1] if r else np.nan
        why = []
        if p in flagged_clo:
            why.append(f'closure {s_:.1f}')
        if np.isfinite(r50) and r50 > ratio_max:
            why.append(f'var ratio {r50:.2f} at {dists[-1]} km')
        if p in flagged_clo and not (np.isfinite(r50) and r50 < 1.0):
            bad.append(p)
            why.append('-> exclude')
        elif why:
            check.append(p)
            why.append('-> check' + (' (correction helps)' if np.isfinite(r50) and r50 < 1.0 else ''))
        a, b = p.split('_')
        dt = (datetime.strptime(b, '%Y%m%d') - datetime.strptime(a, '%Y%m%d')).days
        rows.append([p, dt, n, s_] + (list(r) if r else [np.nan] * len(dists)) + ['; '.join(why)])

    # keep the network connected: drop the worst first, skip a pair whose removal disconnects it
    keep_bridge, exc = [], []
    for p in sorted(bad, key=lambda p: -np.nan_to_num(clo[p][0])):
        if _connected([q for q in pairs if q not in exc + [p]]):
            exc.append(p)
        else:
            keep_bridge.append(p)

    stamp = f'{datetime.now():%Y-%m-%d}'
    head = ['pair', 'dt_days', 'n_triangles', 'closure_rms_rad'] + [f'var_ratio_{d}km' for d in dists] + ['flag']
    with open(os.path.join(c.stack, 'logs', f'ionqc_{stamp}.csv'), 'w', newline='') as f:
        csv.writer(f).writerows([head] + rows)
    old, run24 = _exc_now(c)
    lines = [f'# topsstack.py ionqc {datetime.now():%F %T}  ({len(pairs)} pairs, {ntri} triangles)',
             f'closure RMS: median {med:.2f} rad, MAD {mad:.2f}, threshold {thr:.2f} rad (max({floor}, median + {nmad} MAD))']
    if unw:
        lines.append(f'variance ratio (unw - ion)/unw at {dists} km for {len(cr)} pairs; threshold {ratio_max} at {dists[-1]} km; '
                     f'sign check {sign[0]}/{sign[1]}')
    lines += ['', f"{'pair':18} {'dt':>5} {'ntri':>4} {'closure':>8} " + ' '.join(f'{f"r{d}km":>7}' for d in dists) + '  flag']
    for r in rows:
        if r[-1] or r[0] in old:
            lines.append(f'{r[0]:18} {r[1]:5d} {r[2]:4d} {r[3]:8.2f} ' + ' '.join(f'{x:7.2f}' for x in r[4:-1])
                         + f'  {r[-1] or "(excluded now, not flagged)"}')
    lines += ['', f'suggested --exc_pair ({len(exc)}): {" ".join(sorted(exc)) or "none"}',
              f'to check by eye ({len(check)}): {" ".join(check) or "none"}']
    if keep_bridge:
        lines.append(f'flagged but kept (removal would disconnect the network): {" ".join(keep_bridge)}')
    lines.append(f'now in {os.path.basename(run24) if run24 else "run_24"}: {" ".join(old) or "none"}')
    if apply:
        lines.append(f'applied to: {", ".join(apply_exc(c, exc))} (re-run from step 24)')
    else:
        lines.append('write it with: topsstack.py ionqc TEMPLATE --apply  (then re-run from step 24)')
    txt = '\n'.join(lines)
    open(os.path.join(c.stack, 'logs', f'ionqc_{stamp}.txt'), 'w').write(txt + '\n')
    with _log(c, 'ionqc') as log:
        log.write(txt.splitlines()[0] + f'  suggested: {" ".join(sorted(exc))}\n')
    print(txt)
    return 0
