#!/usr/bin/env python
"""Burst-overlap interferometry (BOI), step 2: per-pixel SBAS time series and along-track velocity.

Input: <stack>/boi/boi_pairs_IW<n>.h5 (boi_pairs.py). Per pair p = (i, j) and pixel x [single-look lines]:
  b_p(x) = o_p(x) - iota_p(x)            (--ion T; --ion none: b = o)
  masked where cor < --cor-min (Li 2021: 0.45) or, with --ion-mask X, where |iota_p - median_x iota_p| > X lines
  (T is unreliable in low-coherence areas, BOI_IMPLEMENTATION.md check 2)
  b_p -= median over the valid pixels of ALL swaths (one constant per pair over the whole scene, never per swath or
  burst: Ma 2022). This replaces the topsStack ESD date constants S_d (computed with the wrong freq factor).
SBAS per pixel: b = A d, d = displacement per date (stack reference date = 0), A rows +1 at j, -1 at i; least squares
over the pixel's valid pairs, with a weak first-difference damping (--damp, relative to the mean number of pairs per
date) so that dates without data are interpolated instead of making the system singular.
Velocity: linear fit of d(t) [lines/yr] x dx_az [m/line] -> mm/yr; sigma_v = residual std / sqrt(sum (t - tbar)^2).
Sign [verify, synthetic test 5.3]: v > 0 = displacement toward later azimuth lines (direction of flight?).

Output: <out>/boi_vel_IW<n>.h5 (lon, lat, v, sigma_v, n_pairs, cor, ts optional), <out>/boi_vel.txt (lon lat v sigma
[mm/yr], np.loadtxt format as Li 2024 / scripts/qc/boi_common.load_boi; the velocity is NOT referenced to any plate,
no heading sign applied), <out>/pic/boi_vel.png.
Usage: boi_ts.py <stack>/boi [--ion T] [--cor-min 0.45] [--min-pairs 100] [--ion-mask 0.003] [--tag name]
"""
import os, glob, time, argparse
from datetime import datetime
import numpy as np
import h5py


def get_args(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('boidir')
    p.add_argument('--ion', choices=['none', 'T'], default='none')
    p.add_argument('--cor-min', type=float, default=0.45)
    p.add_argument('--min-pairs', type=int, default=100)
    p.add_argument('--ion-mask', type=float, default=None, help='drop obs with |iota - median| > X lines')
    p.add_argument('--damp', type=float, default=1e-3)
    p.add_argument('--chunk', type=int, default=4000)
    p.add_argument('--save-ts', action='store_true')
    p.add_argument('--tag', default=None, help='output name suffix (default: ion_<ion>)')
    p.add_argument('--out', default=None, help='default: boidir')
    return p.parse_args(iargs)


def obs(h, n, a):
    """observation row n: b = o - iota (opt.), NaN where masked."""
    b = h['o'][n].astype(np.float64)
    b[h['cor'][n].astype(np.float32) < a.cor_min] = np.nan
    if a.ion == 'T' and h.attrs['has_ion']:
        io = h['iota'][n].astype(np.float64)
        b = b - io
        if a.ion_mask is not None:
            b[np.abs(io - np.nanmedian(io)) > a.ion_mask] = np.nan
    return b


def main(iargs=None):
    a = get_args(iargs)
    tag = a.tag or f'ion_{a.ion}'
    out = a.out or a.boidir
    os.makedirs(f'{out}/pic', exist_ok=True)
    files = sorted(glob.glob(f'{a.boidir}/boi_pairs_IW?.h5'))
    H = {int(f[-4]): h5py.File(f, 'r') for f in files}
    h0 = next(iter(H.values()))
    pairs = h0['pairs'][:].astype(str); dates = h0['dates'][:].astype(str); ref = h0.attrs['ref_date']
    npair, nd = len(pairs), len(dates)
    t = np.array([(datetime.strptime(d, '%Y%m%d') - datetime.strptime(dates[0], '%Y%m%d')).days / 365.25 for d in dates])
    di = {d: k for k, d in enumerate(dates)}
    A = np.zeros((npair, nd))
    for n, p in enumerate(pairs):
        i, j = p.split('_'); A[n, di[j]] += 1; A[n, di[i]] -= 1
    keep = np.array([d != ref for d in dates])
    Ar = A[:, keep]; m = Ar.shape[1]
    P = (Ar[:, :, None] * Ar[:, None, :]).reshape(npair, m * m)
    D = np.diff(np.eye(nd), axis=0)[:, keep]
    R = D.T @ D * a.damp * np.abs(A).sum(0).mean()
    print(f'{npair} pairs, {nd} dates ({t[-1]:.2f} yr), ref {ref}, ion {a.ion}, cor >= {a.cor_min}, '
          f'ion-mask {a.ion_mask}, swaths {list(H)}')

    # pass 1: one constant per pair over the whole scene
    T0 = time.time()
    c = np.zeros(npair)
    for n in range(npair):
        b = np.concatenate([obs(h, n, a) for h in H.values()])
        c[n] = np.nanmedian(b) if np.isfinite(b).any() else np.nan
    print(f'pair constants: median {np.nanmedian(c):+.5f}, std {np.nanstd(c):.5f} lines ({time.time() - T0:.0f} s)')

    txt = []
    for sw, h in H.items():
        T0 = time.time()
        npx = h['o'].shape[1]
        B = np.empty((npair, npx), np.float32)
        for n in range(npair):
            B[n] = obs(h, n, a) - c[n]
        W = np.isfinite(B)
        cor = np.nanmean(np.where(W, h['cor'][:].astype(np.float32), np.nan), axis=0) if W.any() else None
        npairs = W.sum(0)
        V = np.full(npx, np.nan); S = np.full(npx, np.nan)
        TS = np.full((npx, nd), np.nan, np.float32) if a.save_ts else None
        idx = np.where(npairs >= a.min_pairs)[0]
        tb = t - t.mean(); stt = (tb ** 2).sum()
        for k in range(0, idx.size, a.chunk):
            ii = idx[k:k + a.chunk]
            w = W[:, ii].T.astype(np.float64)                         # (nc, npair)
            bb = np.where(W[:, ii], B[:, ii], 0).T.astype(np.float64)
            N = (w @ P).reshape(-1, m, m) + R
            rhs = bb @ Ar
            x = np.linalg.solve(N, rhs[:, :, None])[:, :, 0]
            d = np.zeros((ii.size, nd)); d[:, keep] = x
            v = (d - d.mean(1, keepdims=True)) @ tb / stt
            res = d - d.mean(1, keepdims=True) - v[:, None] * tb[None, :]
            V[ii] = v; S[ii] = res.std(1, ddof=2) / np.sqrt(stt)
            if TS is not None: TS[ii] = d
        dx = h['dx_az'][:]
        vmm, smm = V * dx * 1e3, S * dx * 1e3
        with h5py.File(f'{out}/boi_vel_IW{sw}_{tag}.h5', 'w') as g:
            for k_, v_ in dict(lon=h['lon'][:], lat=h['lat'][:], v=vmm.astype(np.float32), sigma_v=smm.astype(np.float32),
                               v_lines=V.astype(np.float32), n_pairs=npairs.astype(np.int16),
                               cor=cor.astype(np.float32), ovl=h['ovl'][:], dates=h['dates'][:]).items():
                g[k_] = v_
            if TS is not None: g['ts_lines'] = TS
            g.attrs.update(ion=a.ion, cor_min=a.cor_min, min_pairs=a.min_pairs, ion_mask=str(a.ion_mask), damp=a.damp,
                           ref_date=ref, units='mm/yr along track; sign [verify]')
        ok = np.isfinite(vmm)
        txt.append(np.c_[h['lon'][:][ok], h['lat'][:][ok], vmm[ok], smm[ok]])
        q = np.percentile(vmm[ok], [2, 50, 98]) if ok.any() else [np.nan] * 3
        print(f'IW{sw}: valid {ok.mean():.3f} of {npx}  v median {q[1]:+.2f}  std {np.nanstd(vmm):.2f}  '
              f'p2-p98 {q[0]:+.2f}..{q[2]:+.2f} mm/yr  sigma_v median {np.nanmedian(smm):.2f}  '
              f'n_pairs median {np.median(npairs[ok]) if ok.any() else 0:.0f}  ({time.time() - T0:.0f} s)', flush=True)
    X = np.concatenate(txt)
    np.savetxt(f'{out}/boi_vel_{tag}.txt', X, fmt='%.5f %.5f %.3f %.3f',
               header=f'lon lat v sigma [mm/yr], BOI along-track, sign [verify], not plate-referenced; ion {a.ion}, '
                      f'cor>={a.cor_min}, {len(dates)} dates {dates[0]}-{dates[-1]}')
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    lim = np.percentile(np.abs(X[:, 2] - np.median(X[:, 2])), 98)
    fig, ax = plt.subplots(figsize=(6, 9))
    sc = ax.scatter(X[:, 0], X[:, 1], c=X[:, 2] - np.median(X[:, 2]), s=0.3, cmap='RdBu_r', vmin=-lim, vmax=lim,
                    rasterized=True)
    plt.colorbar(sc, ax=ax, shrink=0.6, label='BOI along-track velocity - median [mm/yr]')
    ax.set_aspect(1 / np.cos(np.radians(np.median(X[:, 1])))); ax.set_xlabel('lon'); ax.set_ylabel('lat')
    ax.set_title(f'BOI {dates[0]}-{dates[-1]}, ion {a.ion}, median {np.median(X[:, 2]):+.1f} mm/yr removed', fontsize=9)
    fig.savefig(f'{out}/pic/boi_vel_{tag}.png', dpi=200, bbox_inches='tight')
    print('wrote', f'{out}/boi_vel_IW*_{tag}.h5', f'{out}/boi_vel_{tag}.txt', f'{out}/pic/boi_vel_{tag}.png')


if __name__ == '__main__':
    main()
