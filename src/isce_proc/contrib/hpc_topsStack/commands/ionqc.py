"""`topsstack.py ionqc`: flag bad ionosphere pairs, before invertIon (step 24).

Tests per ion pair (phase in rad), each on the pixels filtIon trusts: ion_cal/filt_msk_init.rdr
(valid, coherence > 0.75, land, height < 5000 m, common sub-band connected component; -1 = valid).
Without that file (no wbdfile in the filtIon config): raw_no_projection.cor > 0.75.

--raw   (after step 22) spread of the raw ionosphere raw_no_projection.ion: robust range
        p99.5 - p0.5. Unwrapping errors inside a sub-swath and bad swath offsets give blocks of
        +-hundreds of rad, i.e. a range many times the stack median. Needs no other pair.
closure (after step 23, default) for every triangle of pairs (a,b), (b,c), (a,c):
            e = I(a,b) + I(b,c) - I(a,c)          (0 if consistent; a constant is removed)
        on filt.ion. A pair's score is the median RMS of e over its triangles, attributed greedily
        (worst pair first; its triangles are then ignored when scoring the others).
--unw   (after step 23) semivariogram gamma(r) = 0.5 E[(phi(x+r) - phi(x))^2] of the interferogram
        (merged/interferograms/<pair>/filt_fine.unw, pixels with filt_fine.cor > 0.5 in the mask)
        at r = 10/30/50 km, ratio (unw - ion) / unw. > 1: the correction adds variance.

Exclude a pair when closure or --raw flags it and the correction does not help (ratio >= 1 or not
tested), and only if the network stays connected without it (union-find over the dates, worst pair
first). Other flags are listed to check by eye. All pairs and statistics go to
logs/ionqc_<date>.csv, the summary to logs/ionqc_<date>.txt. --apply adds the excluded pairs to
--exc_pair of run_24_invertIon and run_26_invertIonShift, keeping pairs already listed there.
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


def _band(f, band, nbands, step=1, dtype=np.float32):
    """Band (1-based) of a BIL ISCE file, every `step`-th pixel; 0 -> NaN for floats."""
    w, l = _size(f + '.xml')
    a = np.fromfile(f, dtype=dtype).reshape(nbands * l, w)[band - 1::nbands][::step, ::step]
    return np.where(a == 0, np.nan, a) if np.issubdtype(dtype, np.floating) else a


def _mask(c, p, step=1):
    """True where filtIon trusts the ionosphere of pair p (see module doc)."""
    d = os.path.join(c.stack, 'ion', p, 'ion_cal')
    f = os.path.join(d, 'filt_msk_init.rdr')
    if os.path.isfile(f):
        return _band(f, 1, 1, step, np.int8) == -1
    cor = _band(os.path.join(d, 'raw_no_projection.cor'), 2, 2, step)
    return np.nan_to_num(cor) > 0.75


def _ion(c, p, name='filt.ion', step=1):
    a = _band(os.path.join(c.stack, 'ion', p, 'ion_cal', name), 2, 2, step)
    return np.where(_mask(c, p, step), a, np.nan)


def _connected(pairs):
    """True if the pairs connect all their dates (union-find)."""
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


def _robust(v, floor, nmad):
    v = np.asarray([x for x in v if np.isfinite(x)])
    med, mad = np.median(v), 1.4826 * np.median(np.abs(v - np.median(v)))
    return med, mad, max(floor, med + nmad * mad)


# ---------------------------------------------------------------- raw spread
def raw_spread(c, pairs, nproc, step=4):
    def one(p):
        a = _ion(c, p, 'raw_no_projection.ion', step)
        a = a[np.isfinite(a)]
        return float(np.percentile(a, 99.5) - np.percentile(a, 0.5)) if a.size > 100 else np.nan
    with ThreadPoolExecutor(nproc) as ex:
        return dict(zip(pairs, ex.map(one, pairs)))


# ---------------------------------------------------------------- loop closure
def closure(ion, nproc):
    """Closure RMS of every triangle of ion pairs: [(triangle, rms)]."""
    pairs = set(ion)
    by_ref = {}
    for p in pairs:
        a, b = p.split('_')
        by_ref.setdefault(a, set()).add(b)
    tris = [(f'{a}_{b}', f'{b}_{c}', f'{a}_{c}')
            for a, bs in by_ref.items() for b, c in combinations(sorted(bs), 2)
            if f'{b}_{c}' in pairs]

    def rms(t):
        e = ion[t[0]] + ion[t[1]] - ion[t[2]]          # NaN outside any of the three masks
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
    med, mad, thr = _robust([x for x, _ in scores(set()).values()], floor, nmad)
    flagged = []
    while True:
        s = scores(set(flagged))
        cand = [(x, p) for p, (x, _) in s.items() if p not in flagged and np.isfinite(x) and x > thr]
        if not cand:
            return s, flagged, (med, mad, thr)
        flagged.append(max(cand)[1])


# ---------------------------------------------------------------- correction test
def _semivar(phi, lags):
    """0.5 mean squared difference for pixel offsets `lags` (list of (dy, dx)); NaN-aware."""
    out = []
    for dy, dx in lags:
        a = phi[max(dy, 0):phi.shape[0] + min(dy, 0), max(dx, 0):phi.shape[1] + min(dx, 0)]
        b = phi[max(-dy, 0):phi.shape[0] + min(-dy, 0), max(-dx, 0):phi.shape[1] + min(-dx, 0)]
        d = (a - b)[np.isfinite(a - b)]
        out.append(0.5 * np.mean(d ** 2) if d.size > 100 else np.nan)
    return np.nanmean(out)


def _geom(c, name, shape):
    from skimage.transform import resize
    f = os.path.join(c.stack, 'merged', 'geom_reference', f'{name}.rdr')
    w, l = _size(f + '.xml')
    return resize(np.fromfile(f, dtype=np.float64).reshape(l, w), shape, order=1,
                  preserve_range=True, anti_aliasing=False)


def correction(c, pairs, shape, dists, nproc, cor_min=0.5):
    """Semivariogram ratio (unw - ion) / unw at `dists` km for the pairs with an interferogram.

    Convention: corrected = unw - ion (Liang et al., 2019); ~20 pairs are also tested with + ion
    as a check of that sign. Pixels: ion mask and interferogram coherence > cor_min.
    """
    from skimage.transform import resize
    lat, lon = _geom(c, 'lat', shape), _geom(c, 'lon', shape)
    dy_km = np.nanmedian(np.abs(np.diff(lat, axis=0))) * 111.2
    dx_km = np.hypot(np.nanmedian(np.abs(np.diff(lat, axis=1))) * 111.2,
                     np.nanmedian(np.abs(np.diff(lon, axis=1))) * 111.2 * np.cos(np.radians(np.nanmean(lat))))
    lags = {r: [(int(round(r * np.sin(t) / dy_km)), int(round(r * np.cos(t) / dx_km)))
                for t in np.linspace(0, np.pi, 8, endpoint=False)] for r in dists}
    ifg_dir = os.path.join(c.stack, 'merged', 'interferograms')
    have = [p for p in pairs if os.path.isfile(os.path.join(ifg_dir, p, 'filt_fine.unw'))]
    check = set(have[::max(1, len(have) // 20)][:20])          # ~20 pairs spread in time

    def to_ion_grid(a):
        return resize(a, shape, order=0, preserve_range=True, anti_aliasing=False)

    def one(p):
        d = os.path.join(ifg_dir, p)
        unw = to_ion_grid(_band(os.path.join(d, 'filt_fine.unw'), 2, 2))
        cor = to_ion_grid(_band(os.path.join(d, 'filt_fine.cor'), 1, 1))
        ion = _ion(c, p)
        unw = np.where(np.isfinite(ion) & (np.nan_to_num(cor) > cor_min), unw, np.nan)
        g0 = [_semivar(unw, lags[r]) for r in dists]
        out = {'minus': [_semivar(unw - ion, lags[r]) / g for r, g in zip(dists, g0)]}
        if p in check:
            out['plus'] = [_semivar(unw + ion, lags[r]) / g for r, g in zip(dists, g0)]
        return p, out
    with ThreadPoolExecutor(nproc) as ex:
        out = dict(ex.map(one, have))
    chk = [(v['minus'][-1], v['plus'][-1]) for v in out.values() if 'plus' in v]
    return ({p: v['minus'] for p, v in out.items()}, (sum(m < pl for m, pl in chk), len(chk)),
            (dy_km, dx_km))


# ---------------------------------------------------------------- run files
def _exc_now(c):
    run = sorted(glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_invertIon')))
    if not run:
        return [], None
    m = re.search(r'--exc_pair((?:\s+\d{8}_\d{8})+)', open(run[0]).read())
    return (m.group(1).split() if m else []), run[0]


def apply_exc(c, exc):
    """Set --exc_pair in run_NN_invertIon and run_NN_invertIonShift to `exc` (replaces the list)."""
    done = []
    for f in sorted(glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_invertIon*'))):
        if not re.fullmatch(r'run_\d\d_invertIon(Shift)?', os.path.basename(f)):
            continue
        s = re.sub(r'\s+--exc_pair(\s+\d{8}_\d{8})+', '', open(f).read().rstrip('\n'))
        if exc:
            s += ' --exc_pair ' + ' '.join(sorted(exc))
        open(f, 'w').write(s + '\n')
        done.append(os.path.basename(f))
    return done


# ---------------------------------------------------------------- driver
def ionqc(c, raw=False, closure_test=True, unw=False, apply=False, nproc=8, step=2,
          floor=3.0, nmad=6.0, ratio_max=1.2, raw_floor=50.0, raw_nmad=10.0,
          dists=(10, 30, 50), sample=0):
    name = 'filt.ion' if closure_test or unw else 'raw_no_projection.ion'
    files = sorted(glob.glob(os.path.join(c.stack, 'ion', '*_*', 'ion_cal', name)))
    pairs = sorted(f.split(os.sep)[-3] for f in files if PAIR.match(f.split(os.sep)[-3]))
    if not pairs:
        raise SystemExit(f'no ion/*_*/ion_cal/{name}: run the ionosphere steps first (22 for --raw, 23 otherwise)')
    old, run24 = _exc_now(c)
    stats = [f'# topsstack.py ionqc {datetime.now():%F %T}  ({len(pairs)} pairs)']

    rs, raw_bad = {}, []
    if raw:
        print(f'raw spread of {len(pairs)} raw_no_projection.ion ...', flush=True)
        rs = raw_spread(c, pairs, nproc)
        rmed, rmad, rthr = _robust(rs.values(), raw_floor, raw_nmad)
        raw_bad = [p for p, v in rs.items() if np.isfinite(v) and v > rthr]
        stats.append(f'raw spread p99.5-p0.5: median {rmed:.1f} rad, MAD {rmad:.1f}, threshold {rthr:.1f} '
                     f'(max({raw_floor}, median + {raw_nmad} MAD)); {len(raw_bad)} flagged')

    clo, clo_bad, ntri = {p: (np.nan, 0) for p in pairs}, [], 0
    if closure_test:
        print(f'closure of {len(pairs)} filt.ion (every {step}. pixel, masked) ...', flush=True)
        with ThreadPoolExecutor(nproc) as ex:
            ion = dict(zip(pairs, ex.map(lambda p: _ion(c, p, step=step), pairs)))
        tri_rms = closure(ion, nproc)
        ntri = len(tri_rms)
        clo, clo_bad, (med, mad, thr) = attribute(pairs, tri_rms, floor, nmad)
        stats.append(f'closure RMS over {ntri} triangles: median {med:.2f} rad, MAD {mad:.2f}, threshold {thr:.2f} '
                     f'(max({floor}, median + {nmad} MAD)); {len(clo_bad)} flagged')

    cr = {}
    if unw:
        shape = _band(os.path.join(c.stack, 'ion', pairs[0], 'ion_cal', 'filt.ion'), 2, 2).shape
        flag = sorted(set(clo_bad) | set(raw_bad))
        rng = np.random.default_rng(0)
        sub = pairs if not sample or sample >= len(pairs) else sorted(set(flag) | set(rng.choice(pairs, sample, replace=False)))
        print(f'correction test on the interferograms of {len(sub)} pairs ...', flush=True)
        cr, sign, sp = correction(c, sub, shape, dists, nproc)
        stats.append(f'correction test on {len(cr)} interferograms (pixel {sp[0]:.2f} x {sp[1]:.2f} km): '
                     f'ratio (unw - ion)/unw at {dists} km, threshold {ratio_max} at {dists[-1]} km; '
                     f'sign check: unw - ion better than unw + ion in {sign[0]}/{sign[1]}')

    rows, bad, check = [], [], []
    for p in pairs:
        s_, n = clo[p]
        r = cr.get(p)
        r50 = r[-1] if r else np.nan
        helps = np.isfinite(r50) and r50 < 1.0
        why = ([f'raw {rs[p]:.0f}'] if p in raw_bad else []) + ([f'closure {s_:.1f}'] if p in clo_bad else [])
        if np.isfinite(r50) and r50 > ratio_max:
            why.append(f'var ratio {r50:.2f}')
        if (p in raw_bad or p in clo_bad) and not helps:
            bad.append(p)
            why.append('-> exclude')
        elif why:
            check.append(p)
            why.append('-> check' + (' (correction helps)' if helps else ''))
        a, b = p.split('_')
        dt = (datetime.strptime(b, '%Y%m%d') - datetime.strptime(a, '%Y%m%d')).days
        rows.append([p, dt, rs.get(p, np.nan), n, s_] + (list(r) if r else [np.nan] * len(dists)) + ['; '.join(why)])

    # keep the network connected: remove the worst first; keep a pair whose removal splits it
    def worst(p):
        return -(np.nan_to_num(clo[p][0]) + np.nan_to_num(rs.get(p, 0)))
    exc, keep_bridge = [q for q in old if q in pairs], []
    for p in sorted(bad, key=worst):
        if p in exc:
            continue
        if _connected([q for q in pairs if q not in exc + [p]]):
            exc.append(p)
        else:
            keep_bridge.append(p)

    stamp = f'{datetime.now():%Y-%m-%d}'
    head = ['pair', 'dt_days', 'raw_spread_rad', 'n_triangles', 'closure_rms_rad'] + [f'var_ratio_{d}km' for d in dists] + ['flag']
    with open(os.path.join(c.stack, 'logs', f'ionqc_{stamp}.csv'), 'w', newline='') as f:
        csv.writer(f).writerows([head] + rows)
    lines = stats + ['', f"{'pair':18} {'dt':>5} {'raw':>7} {'ntri':>4} {'closure':>8} "
                     + ' '.join(f'{f"r{d}km":>7}' for d in dists) + '  flag']
    for r in rows:
        if r[-1] or r[0] in old:
            lines.append(f'{r[0]:18} {r[1]:5d} {r[2]:7.1f} {r[3]:4d} {r[4]:8.2f} ' + ' '.join(f'{x:7.2f}' for x in r[5:-1])
                         + f'  {r[-1] or "(already excluded, not flagged now)"}')
    new = sorted(set(exc) - set(old))
    lines += ['', f'exclude ({len(exc)}): {" ".join(sorted(exc)) or "none"}',
              f'  already in {os.path.basename(run24) if run24 else "run_24"}: {" ".join(old) or "none"}; new: {" ".join(new) or "none"}',
              f'check by eye ({len(check)}): {" ".join(check) or "none"}']
    if keep_bridge:
        lines.append(f'flagged but kept (removal would disconnect the network): {" ".join(keep_bridge)}')
    if apply and new:
        lines.append(f'applied to: {", ".join(apply_exc(c, exc))} (re-run from step 24)')
    elif apply:
        lines.append('nothing new to apply')
    else:
        lines.append('write it with --apply (then re-run from step 24)')
    txt = '\n'.join(lines)
    open(os.path.join(c.stack, 'logs', f'ionqc_{stamp}.txt'), 'w').write(txt + '\n')
    with _log(c, 'ionqc') as log:
        log.write(txt.splitlines()[0] + f'  exclude: {" ".join(sorted(exc))}{"  (applied)" if apply and new else ""}\n')
    print(txt)
    return 0
