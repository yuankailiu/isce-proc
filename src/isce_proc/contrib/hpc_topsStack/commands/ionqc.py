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
        (worst pair first; its triangles are then ignored when scoring the others). A flagged pair
        with no triangle free of other flagged pairs is 'ambiguous' ('closure x?').
--network (optional, report only) residual of each pair against the network consensus, iteratively
        reweighted (IRLS as chile/src/chile/qc/ion_network.py) over the pairs still in use; a pair with
        weight < 0.5 and residual > 0.3 x its own signal is listed to look at, never excluded. It flags
        different pairs than closure (a018_south: 2 of 15 in common), so it is a second opinion.
--cover (after step 23) regional coverage: the fraction of each ~block x block pixel area (default 100 px, about
        the filtIon window) that filtIon trusts, per pair. Where the stack usually has data (median coverage >=
        0.7) but a pair has < 0.3, filt.ion there is almost all fill; if such a pair is the only link across a
        gap, the fill becomes a velocity (d083 north corner: 39 bridge pairs 2020-21 -> 2025-26 gave +3 mm/yr).
        Scene-wide coverage does not show it (those pairs: 0.60 vs 0.80). Flagged pairs are excluded (network
        kept connected).
--unw   (after step 23) semivariogram gamma(r) = 0.5 E[(phi(x+r) - phi(x))^2] of the interferogram
        (merged/interferograms/<pair>/filt_fine.unw, pixels with filt_fine.cor > 0.5 in the mask)
        at r = 10, 25, 50, 100, 200 km (about log-spaced), ratio (unw - ion) / unw; the decision uses
        100 km, where the filtered ionosphere (filtIon windows ~70-150 km) has its power.
        > 1: the correction adds variance.

Exclude a pair when --cover flags it, or closure or --raw flags it and the correction does not help (ratio >= 1 or not
tested), or closure is ambiguous and --raw or --unw (ratio > --ratio) also flags it, or the
correction alone makes the interferogram much worse (ratio > 5); and only if the network stays connected without it (union-find over the dates, worst pair
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
    m = np.nan_to_num(cor) > 0.75
    for g in ('merged/geom_reference', 'geom_reference'):   # water body at the ion looks: 0 = land
        f = os.path.join(c.stack, g, 'waterBody_ionlk.rdr')
        if os.path.isfile(f):
            w = _band(f, 1, 1, step, np.int8)
            if w.shape == m.shape:
                m &= (w == 0)
            break
    return m


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
            break
        flagged.append(max(cand)[1])
    # re-check: a flagged pair is confirmed only by a triangle without the other flagged pairs.
    # With none left (e.g. it shares its only triangle with a bad pair) closure cannot tell: ambiguous.
    s = scores(set(flagged))
    confirmed = [p for p in flagged if np.isfinite(s[p][0]) and s[p][0] > thr]
    ambiguous = [p for p in flagged if not np.isfinite(s[p][0])]
    s0 = scores(set())
    for p in ambiguous:
        s[p] = (s0[p][0], s0[p][1])                           # report the raw median
    return s, confirmed, ambiguous, (med, mad, thr)


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
    if not os.path.isfile(f + '.xml'):                          # exported stacks: geom_reference/ on top
        f = os.path.join(c.stack, 'geom_reference', f'{name}.rdr')
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


# ---------------------------------------------------------------- seam test
def _invert(ion, used, dates):
    """Per-date ionosphere [n_date, pix] from pair maps by least squares (first date = 0), like
    invertIon.py without weights: I(a,b) = phi_b - phi_a."""
    idx = {d: i for i, d in enumerate(dates)}
    H = np.zeros((len(used), len(dates)))
    for k, q in enumerate(used):
        a, b = q.split('_')
        H[k, idx[a]], H[k, idx[b]] = -1, 1
    obs = np.stack([np.nan_to_num(ion[q]).ravel() for q in used])
    ts = np.zeros((len(dates), obs.shape[1]))
    ts[1:] = np.linalg.lstsq(H[:, 1:], obs, rcond=None)[0]
    return ts


def seam_test(c, pairs, sets, dist, nproc, step=4, span_max=2, dt_max=60, cor_min=0.5):
    """Where the ionospheric network is thin (<= span_max ion pairs span a date boundary), score each
    exclusion set by the interferograms that span the boundary: variance ratio (unw - ion) / unw at
    `dist` km of the inverted per-date ionosphere. Only these interferograms see the block offset a
    thin boundary allows (a120_south 2026-09-22). sets: {'A': excluded pairs, 'B': ...}.
    Returns [(boundary date, {set: (median ratio, n ifgs)}, n spanning ion pairs per set)]."""
    from skimage.transform import resize
    dates = sorted({d for q in pairs for d in q.split('_')})
    spans = {}
    for name, exc in sets.items():
        used = [q for q in pairs if q not in exc]
        spans[name] = {t: sum(q.split('_')[0] < t <= q.split('_')[1] for q in used) for t in dates[1:]}
    thin = [t for t in dates[1:] if min(spans[n][t] for n in sets) <= span_max]
    ifg_dir = os.path.join(c.stack, 'merged', 'interferograms')
    ifgs = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(ifg_dir, '*_*', 'filt_fine.unw')))
    todo = {t: [q for q in ifgs if q.split('_')[0] < t <= q.split('_')[1]
                and (datetime.strptime(q[9:], '%Y%m%d') - datetime.strptime(q[:8], '%Y%m%d')).days <= dt_max]
            for t in thin}
    todo = {t: v for t, v in todo.items() if v}
    if not todo:
        return [], thin
    with ThreadPoolExecutor(nproc) as ex:
        ion = dict(zip(pairs, ex.map(lambda q: _band(os.path.join(c.stack, 'ion', q, 'ion_cal', 'filt.ion'), 2, 2, step), pairs)))
    shape = next(iter(ion.values())).shape
    full = _band(os.path.join(c.stack, 'ion', pairs[0], 'ion_cal', 'filt.ion'), 2, 2).shape
    lat, lon = _geom(c, 'lat', full)[::step, ::step], _geom(c, 'lon', full)[::step, ::step]
    dy = np.nanmedian(np.abs(np.diff(lat, axis=0))) * 111.2
    dx = np.hypot(np.nanmedian(np.abs(np.diff(lat, axis=1))) * 111.2,
                  np.nanmedian(np.abs(np.diff(lon, axis=1))) * 111.2 * np.cos(np.radians(np.nanmean(lat))))
    lags = [(int(round(dist * np.sin(t) / dy)), int(round(dist * np.cos(t) / dx))) for t in np.linspace(0, np.pi, 8, endpoint=False)]
    ts = {n: _invert(ion, [q for q in pairs if q not in exc], dates) for n, exc in sets.items()}
    di = {d: i for i, d in enumerate(dates)}

    def one(q):
        d = os.path.join(ifg_dir, q)
        unw = resize(_band(os.path.join(d, 'filt_fine.unw'), 2, 2), full, order=0, preserve_range=True, anti_aliasing=False)[::step, ::step]
        cor = resize(_band(os.path.join(d, 'filt_fine.cor'), 1, 1), full, order=0, preserve_range=True, anti_aliasing=False)[::step, ::step]
        a, b = q.split('_')
        unw = np.where(np.nan_to_num(cor) > cor_min, unw, np.nan)
        g0 = _semivar(unw, lags)
        return q, {n: (_semivar(unw - (t[di[b]] - t[di[a]]).reshape(shape), lags) / g0 if a in di and b in di else np.nan)
                   for n, t in ts.items()}
    need = sorted({q for v in todo.values() for q in v})
    with ThreadPoolExecutor(nproc) as ex:
        res = dict(ex.map(one, need))
    out = []
    for t, qs in todo.items():
        out.append((t, {n: (float(np.nanmedian([res[q][n] for q in qs])), len(qs)) for n in sets},
                    {n: spans[n][t] for n in sets}))
    return out, thin


# ---------------------------------------------------------------- regional coverage
def region_cover(c, pairs, nproc, block=100, step=2, good=0.7):
    """Per pair, the lowest coverage over the blocks the stack usually covers (median >= good).

    Coverage = fraction of the block that filtIon trusts (_mask: filt_msk_init.rdr, else coh > 0.75 & land).
    Returns ({pair: (min coverage, block row, block col)}, number of well-covered blocks, block size in pixels,
    per-pair block coverage array (n_pair, ny, nx)).
    """
    b = max(1, block // step)

    def one(p):
        m = _mask(c, p, step).astype(np.float32)
        ny, nx = m.shape[0] // b, m.shape[1] // b
        return m[:ny * b, :nx * b].reshape(ny, b, nx, b).mean(axis=(1, 3))
    with ThreadPoolExecutor(nproc) as ex:
        cov = np.array(list(ex.map(one, pairs)))
    med = np.median(cov, axis=0)
    gb = med >= good
    if not gb.any():
        return {}, 0, b * step, cov
    out = {}
    for p, cv in zip(pairs, cov):
        x = np.where(gb, cv, np.inf)
        i, j = np.unravel_index(np.argmin(x), x.shape)
        out[p] = (float(x[i, j]), int(i), int(j))
    return out, int(gb.sum()), b * step, cov


def _linked(a, b, pairs):
    """True if dates a and b are connected by `pairs` (union-find)."""
    par = {}

    def root(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for q in pairs:
        u, v = q.split('_')
        par[root(u)] = root(v)
    return a in par and b in par and root(a) == root(b)


# ---------------------------------------------------------------- network residual (IRLS)
def network(c, pairs, exc, nproc, n_pixel=2500, k=2.0, nit=5):
    """Residual of each pair against the network consensus, iteratively reweighted (--network).

    filt.ion band 2 of every pair at ~n_pixel land samples (waterBody_ionlk 0 = land, if present), pixels
    finite in all pairs in use, per-pair mean removed. Least squares over the pairs in use (not in `exc`),
    then IRLS: r_i = RMS(p_i - (c[d2] - c[d1])), w_i = min(1, k median(r) / r_i), `nit` times (as
    chile/src/chile/qc/ion_network.py). Excluded pairs are scored against the final network, not fitted.
    Returns {pair: (weight, or NaN if excluded; r_i / median(r); r_i / signal_i)}, signal_i = RMS of the
    pair itself. The weight alone favours pairs with a strong ionosphere (a149: flagged pairs had 7x the
    median signal), so the caller also requires r_i / signal_i > 0.3 (the irlsE curation criterion).
    Report only: never excludes.
    """
    f0 = os.path.join(c.stack, 'ion', pairs[0], 'ion_cal', 'filt.ion')
    w, l = _size(f0 + '.xml')
    step = max(1, int(np.sqrt(l * w / n_pixel)))
    with ThreadPoolExecutor(nproc) as ex:
        P = np.array(list(ex.map(lambda p: _band(os.path.join(c.stack, 'ion', p, 'ion_cal', 'filt.ion'), 2, 2, step).ravel(), pairs)))
    for g in ('merged/geom_reference', 'geom_reference'):
        f = os.path.join(c.stack, g, 'waterBody_ionlk.rdr')
        if os.path.isfile(f):
            land = _band(f, 1, 1, step, np.int8).ravel() == 0
            if land.size == P.shape[1]:
                P[:, ~land] = np.nan
            break
    live = np.isfinite(P).any(axis=1)
    use = np.array([p not in exc for p in pairs]) & live
    good = np.isfinite(P[use]).all(axis=0)
    if good.sum() < 200:
        print(f'network test skipped: only {good.sum()} pixels finite in every pair in use')
        return {}
    P = P[:, good]
    P[live] -= np.nanmean(P[live], axis=1, keepdims=True)
    dates = sorted({d for p, u in zip(pairs, use) if u for d in p.split('_')})
    ix = {d: i for i, d in enumerate(dates)}
    A = np.zeros((len(pairs), len(dates)))
    for i, p in enumerate(pairs):
        a, b = p.split('_')
        if a in ix and b in ix:
            A[i, ix[a]], A[i, ix[b]] = -1, 1
    Au, Pu, wt = A[use], P[use], np.ones(use.sum())
    for _ in range(nit):
        s = np.sqrt(wt)[:, None]
        cc = np.linalg.lstsq(Au * s, Pu * s, rcond=None)[0]
        r = np.sqrt(((Pu - Au @ cc) ** 2).mean(axis=1))
        wt = np.minimum(1.0, k * np.median(r) / np.maximum(r, 1e-12))
    med = np.median(r)
    with np.errstate(invalid='ignore'):
        sig = np.sqrt(np.nanmean(P ** 2, axis=1))          # NaN for all-empty pairs
    out = {p: (float(x), float(y / med), float(y / g)) for p, x, y, g in zip(np.array(pairs)[use], wt, r, sig[use])}
    for i in np.where(~use & live)[0]:                     # excluded: predicted by the network, not fitted
        if A[i].any():
            e = P[i] - A[i] @ cc
            e = e[np.isfinite(e)]
            ri = float(np.sqrt(np.mean((e - e.mean()) ** 2))) if e.size else np.nan
            out[pairs[i]] = (np.nan, ri / med, ri / sig[i])
    return out


# ---------------------------------------------------------------- run files
def _exc_now(c):
    run = sorted(glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_invertIon')))
    if not run:
        return [], None
    txt = '\n'.join(l for l in open(run[0]) if l.strip() and not l.lstrip().startswith('#'))
    m = re.search(r'--exc_pair((?:\s+\d{8}_\d{8})+)', txt)
    d = re.search(r'--exc_date((?:\s+\d{8})+)', txt)
    if d:
        print(f'note: {os.path.basename(run[0])} also has --exc_date {d.group(1).strip()}: those epochs get no '
              'ionospheric correction at all; prefer excluding pairs')
    return (m.group(1).split() if m else []), run[0]


def apply_exc(c, exc):
    """Set --exc_pair in run_NN_invertIon and run_NN_invertIonShift to `exc` (replaces the list)."""
    done = []
    for f in sorted(glob.glob(os.path.join(c.stack, 'run_files', 'run_[0-9][0-9]_invertIon*'))):
        if not re.fullmatch(r'run_\d\d_invertIon(Shift)?', os.path.basename(f)):
            continue
        lines = open(f).read().splitlines()
        i = next(k for k, l in enumerate(lines) if l.strip() and not l.lstrip().startswith('#'))   # the command
        lines[i] = re.sub(r'\s+--exc_pair(\s+\d{8}_\d{8})+', '', lines[i]) + (' --exc_pair ' + ' '.join(sorted(exc)) if exc else '')
        open(f, 'w').write('\n'.join(lines) + '\n')
        done.append(os.path.basename(f))
    return done


# ---------------------------------------------------------------- driver
def ionqc(c, raw=False, closure_test=True, unw=False, apply=False, nproc=8, step=2,
          floor=3.0, nmad=6.0, ratio_max=1.5, ratio_excl=5.0, raw_floor=50.0, raw_nmad=10.0,
          dists=(10, 25, 50, 100, 200), dist_test=100, sample=0, network_test=False, w_check=0.5, rel_check=0.3,
          cover_test=False, cover_block=100, cover_low=0.3, cover_good=0.7):
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

    clo, clo_bad, clo_amb, ntri = {p: (np.nan, 0) for p in pairs}, [], [], 0
    if closure_test:
        print(f'closure of {len(pairs)} filt.ion (every {step}. pixel, masked) ...', flush=True)
        with ThreadPoolExecutor(nproc) as ex:
            ion = dict(zip(pairs, ex.map(lambda p: _ion(c, p, step=step), pairs)))
        tri_rms = closure(ion, nproc)
        ntri = len(tri_rms)
        clo, clo_bad, clo_amb, (med, mad, thr) = attribute(pairs, tri_rms, floor, nmad)
        stats.append(f'closure RMS over {ntri} triangles: median {med:.2f} rad, MAD {mad:.2f}, threshold {thr:.2f} '
                     f'(max({floor}, median + {nmad} MAD)); {len(clo_bad)} flagged, {len(clo_amb)} ambiguous (no independent triangle)')

    cr = {}
    if unw:
        shape = _band(os.path.join(c.stack, 'ion', pairs[0], 'ion_cal', 'filt.ion'), 2, 2).shape
        flag = sorted(set(clo_bad) | set(clo_amb) | set(raw_bad))
        rng = np.random.default_rng(0)
        sub = pairs if not sample or sample >= len(pairs) else sorted(set(flag) | set(rng.choice(pairs, sample, replace=False)))
        print(f'correction test on the interferograms of {len(sub)} pairs ...', flush=True)
        cr, sign, sp = correction(c, sub, shape, dists, nproc)
        stats.append(f'correction test on {len(cr)} interferograms (pixel {sp[0]:.2f} x {sp[1]:.2f} km): '
                     f'ratio (unw - ion)/unw at {dists} km, decision at {dist_test} km (list > {ratio_max}); '
                     f'sign check: unw - ion better than unw + ion in {sign[0]}/{sign[1]}')

    cov, cov_bad, cov_alt = {}, [], []
    if cover_test:
        print(f'regional coverage of {len(pairs)} pairs ({cover_block} px blocks) ...', flush=True)
        cov, ngood, bpx, carr = region_cover(c, pairs, nproc, cover_block, step, cover_good)
        low = [p for p, v in cov.items() if v[0] < cover_low]
        # exclude only where real data can replace the fill: the pair's dates stay linked, in its worst block,
        # by pairs with coverage >= cover_low there (not already excluded); otherwise only list it
        ipair = {p: k for k, p in enumerate(pairs)}
        cov_alt = []
        for p in low:
            _, i, j = cov[p]
            ok = [q for q in pairs if q != p and q not in old and carr[ipair[q], i, j] >= cover_low]
            if _linked(*p.split('_'), ok):
                cov_bad.append(p)
            else:
                cov_alt.append(p)
        stats.append(f'regional coverage: {ngood} blocks of {bpx} px with stack median >= {cover_good}; '
                     f'{len(low)} pairs below {cover_low} in one of them; {len(cov_bad)} replaceable by covered pairs')

    rows, bad, check = [], [], []
    for p in pairs:
        s_, n = clo[p]
        r = cr.get(p)
        r50 = r[dists.index(dist_test)] if r else np.nan
        helps = np.isfinite(r50) and r50 < 1.0
        worse = np.isfinite(r50) and r50 > ratio_max
        why = (([f'raw {rs[p]:.0f}'] if p in raw_bad else []) + ([f'closure {s_:.1f}'] if p in clo_bad else [])
               + ([f'closure {s_:.1f}?'] if p in clo_amb else []))
        if worse:
            why.append(f'var ratio {r50:.2f}')
        if p in cov_bad:
            why.append(f'region cover {cov[p][0]:.2f} (block {cov[p][1]},{cov[p][2]})')
        elif cover_test and p in cov_alt:
            why.append(f'region cover {cov[p][0]:.2f} (block {cov[p][1]},{cov[p][2]}), no covered alternative')
        if p in cov_bad or (((p in raw_bad or p in clo_bad) and not helps) or (p in clo_amb and (p in raw_bad or worse))
                or (np.isfinite(r50) and r50 > ratio_excl)):
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
        return -(np.nan_to_num(clo[p][0]) + np.nan_to_num(rs.get(p, 0)) + (1 - cov[p][0] if p in cov_bad else 0))
    exc, keep_bridge = [q for q in old if q in pairs], []
    for p in sorted(bad, key=worst):
        if p in exc:
            continue
        if _connected([q for q in pairs if q not in exc + [p]]):
            exc.append(p)
        else:
            keep_bridge.append(p)

    # seam test: where the network is thin, compare the current exclusions (A) with the proposal (B)
    # on the interferograms spanning the thin boundary; drop proposed exclusions that make it worse
    seams, seam_note = [], []
    if unw:
        A, B = set(q for q in old if q in pairs), set(exc)
        seams, thin = seam_test(c, pairs, {'A': A, 'B': B}, dist_test, nproc)
        for t, sc, sp in seams:
            (ra, n), (rb, _) = sc['A'], sc['B']
            worse = A != B and rb > 1.05 * ra
            seam_note.append(f'  {t}: ion pairs spanning A {sp["A"]} / B {sp["B"]}; {n} interferograms, '
                             f'ratio A {ra:.2f} / B {rb:.2f}' + (' -> B worse, keep A there' if worse else ''))
            if worse:
                back = [q for q in exc if q not in A and q.split('_')[0] < t <= q.split('_')[1]]
                exc = [q for q in exc if q not in back]
                back_ = [q for q in back]
                for q in back_:
                    if q in bad:
                        bad.remove(q)
                    check.append(q)

    # pairs closure cannot validate: no triangle, or a bridge of the network in use (removing it splits
    # the epochs). There the pair IS the network's prediction; judge them with interferograms that span
    # them, and do not restore an excluded one on closure alone (a120_south 2026-09-22).
    used = [q for q in pairs if q not in exc]
    bridges = [q for q in used if not _connected([x for x in used if x != q])] if len(used) < 3000 else []
    weak = set(bridges) | {q for q in pairs if clo[q][1] == 0}
    new = sorted(set(exc) - set(old))
    clean_old = sorted(q for q in old if q in pairs and q not in bad and q not in weak)

    # --network (report only): pairs the network consensus down-weights, among those still in use
    net, net_check = {}, []
    if network_test:
        print(f'network residual (IRLS) of {len(used)} pairs in use ...', flush=True)
        net = network(c, pairs, set(exc), nproc)
        for q in used:
            wq, _, rel = net.get(q, (np.nan, np.nan, np.nan))
            if np.isfinite(wq) and wq < w_check and rel > rel_check and q not in check and q not in keep_bridge:
                check.append(q)
                net_check.append(q)
        stats.append(f'network (IRLS, k 2, 5 iterations; report only): {len(net_check)} more pair(s) with weight < {w_check} and residual > {rel_check} x own signal')

    def status(q):
        if q in new: return 'exclude (new)'
        if q in old: return 'excluded (yours, looks clean)' if q in clean_old else 'excluded (yours)'
        if q in keep_bridge: return 'flagged, kept (bridge)'
        if q in check: return 'check'
        return 'ok'

    # one CSV with everything, one short TXT with the decision
    stamp = f'{datetime.now():%Y-%m-%d}'
    os.makedirs(os.path.join(c.stack, 'logs'), exist_ok=True)
    head = (['pair', 'dt_days', 'raw_spread_rad', 'n_triangles', 'closure_rms_rad'] + [f'var_ratio_{d}km' for d in dists]
            + ['irls_weight', 'irls_resid_over_median', 'irls_resid_over_signal', 'min_block_cover', 'status', 'closure_unvalidated', 'reason'])

    def reason(r):
        why = r[-1].replace(' -> exclude', '').replace(' -> check', '').strip(' ;')
        if r[0] in net_check:
            why = '; '.join(x for x in [why, f'network w {net[r[0]][0]:.2f}, resid {net[r[0]][2]:.2f} x signal'] if x)
        return why
    out = [r[:-1] + list(net.get(r[0], (np.nan, np.nan, np.nan))) + [cov.get(r[0], (np.nan,))[0], status(r[0]), int(r[0] in weak), reason(r)] for r in rows]
    fcsv = os.path.join(c.stack, 'logs', f'ionqc_{stamp}.csv')
    with open(fcsv, 'w', newline='') as f:
        csv.writer(f).writerows([head] + out)

    if new:
        decision = f'APPLY {len(new)} new exclusion(s): topsstack.py ionqc TEMPLATE --raw --unw --apply, then submit -s 24'
    elif check:
        decision = f'KEEP; look at {len(check)} pair(s) in the ion figures (orange boxes)'
    else:
        decision = 'KEEP as is'
    a100 = np.array([v[dists.index(dist_test)] for v in cr.values() if np.isfinite(v[dists.index(dist_test)])])
    info = [f'{len(pairs)} pairs', f'excluded now {len(old)}']
    if closure_test:
        info.append(f'closure median {med:.2f} rad (flag > {thr:.1f})')
    if raw:
        info.append(f'raw median {rmed:.0f} rad (flag > {rthr:.0f})')
    if a100.size:
        info.append(f'correction at {dist_test} km: median ratio {np.median(a100):.2f}, helps {100 * (a100 < 1).mean():.0f}%')
    if cover_test:
        info.append(f'regional coverage: {len(cov_bad)} replaceable pair(s) < {cover_low} where the stack has >= {cover_good}, '
                    f'{len(cov_alt)} more without a covered alternative (listed)')
    if network_test:
        info.append(f'network: {len(net_check)} more to look at (w < {w_check}, resid > {rel_check} x signal)')
    byp = {r[0]: r for r in out}

    def show(title, lst):
        if not lst:
            return []
        L = [f'{title} ({len(lst)}):']
        for q in lst[:10]:
            r = byp[q]
            L.append(f'  {q}  dt {r[1]:>4} d  raw {r[2]:5.0f}  closure {r[4]:6.2f}  r{dist_test} {r[5 + dists.index(dist_test)]:6.2f}  {r[-1]}')
        return L + ([f'  ... {len(lst) - 10} more in the CSV'] if len(lst) > 10 else [])
    lines = [f'{"/".join(c.stack.rstrip("/").split("/")[-2:])} {datetime.now():%F %T}: {decision}', '  ' + '; '.join(info)]
    lines += show('new exclusions', new) + show('look at', check)
    lines += show('your exclusions that look clean (restore only after a seam test)', clean_old)
    if seam_note:
        lines += [f'thin boundaries (<= 2 ion pairs span them), scored by spanning interferograms at {dist_test} km '
                  '(ratio < 1: the ionosphere helps; A = current exclusions, B = with the new ones):'] + seam_note
    if keep_bridge:
        lines.append(f'flagged but kept, removal would split the network: {" ".join(keep_bridge)}')
    if apply and new:
        lines.append(f'applied to {", ".join(apply_exc(c, exc))}; re-run from step 24')
    lines.append(f'details: {os.path.relpath(fcsv, c.stack)}')
    txt = '\n'.join(lines)
    open(os.path.join(c.stack, 'logs', f'ionqc_{stamp}.txt'), 'w').write(txt + '\n')
    for f in glob.glob(os.path.join(c.stack, 'logs', 'ionqc_[a-z]*.txt')):     # lists of earlier versions
        if not re.search(r'ionqc_\d{4}-', f):
            os.remove(f)
    with _log(c, 'ionqc') as log:
        log.write(lines[0] + (f'  exclude: {" ".join(sorted(exc))}' if exc else '') + '\n')
    print(txt)
    return 0
