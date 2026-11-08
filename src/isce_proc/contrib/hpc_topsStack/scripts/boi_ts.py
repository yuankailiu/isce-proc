#!/usr/bin/env python
"""Burst-overlap interferometry (BOI), step 2: masks, per-pixel SBAS time series and along-track velocity.

Input: <stack>/boi/boi_pairs_IW<n>.h5 (boi_pairs.py). Per pair p = (i, j), pixel x, single-look lines:
  o_p = Phi_p / F (F reference-only, check 1b),  iota_p = -(T_j - T_i)  (check 2: o ~ iota + motion + const)

1. Ionosphere mask M(x) (--ion T): T is unreliable where the topsStack ionosphere is poorly estimated (check 2;
   filt_msk is 1 at all BOI pixels, so no use). Data-driven: in bins of one overlap x --ion-bin range columns,
   pooled regression over pairs and pixels of (o_p - c_p) on (iota_p - c'_p) (c: per-pair scene medians) gives a
   slope a. M = 1 where |a - 1| <= --ion-tol, else 0 (no ionospheric correction there, as Li 2024).
2. b_p = o_p - M iota_p, masked where cor < --cor-min; minus one constant per pair over ALL swaths (scene median;
   never per swath or burst, Ma 2022). Replaces the topsStack S_d (wrong freq factor).
3. SBAS per pixel (A: +1 at j, -1 at i, reference date 0, weak first-difference damping --damp). Outliers: residuals
   |r - median r| > --kmad * 1.4826 MAD (per pixel) removed, one re-solve.
4. Quality: temporal coherence gamma_t = |mean_p exp(i F r_p)| (F = 2 pi dt freq_hz, rad/line) over the kept pairs;
   pixel kept if gamma_t >= --gt-min and n_pairs >= --min-pairs.
Velocity: linear fit of d(t) [lines/yr] x dx_az -> mm/yr; sigma_v = residual std / sqrt(sum (t - tbar)^2).
Sign [verify, synthetic test 5.3]: v > 0 = displacement toward later azimuth lines.

Output: <out>/boi_vel_IW<n>_<tag>.h5 (lon lat v v_all sigma_v gamma_t n_pairs cor ion_slope ion_M; v NaN where
masked), <out>/boi_vel_<tag>.txt (kept pixels: lon lat v sigma [mm/yr], Li 2024 / boi_common.load_boi format; not
plate-referenced, no heading sign), <out>/pic/boi_vel_<tag>.png (velocity, gamma_t, ion slope).
Usage: boi_ts.py <stack>/boi [--ion T] [--gt-min 0.7] [--tag name]
"""
import os, glob, time, argparse
from datetime import datetime
import numpy as np
import h5py

DT = 2.055556e-3                                     # S1 IW azimuth time interval [s]


def get_args(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('boidir')
    p.add_argument('--ion', choices=['none', 'T'], default='none')
    p.add_argument('--ion-bin', type=int, default=30, help='range columns per ionosphere-slope bin (per overlap)')
    p.add_argument('--ion-tol', type=float, default=0.5, help='M = 1 where |slope - 1| <= tol')
    p.add_argument('--cor-min', type=float, default=0.45)
    p.add_argument('--kmad', type=float, default=3.0)
    p.add_argument('--gt-min', type=float, default=0.7)
    p.add_argument('--min-pairs', type=int, default=100)
    p.add_argument('--damp', type=float, default=1e-3)
    p.add_argument('--chunk', type=int, default=4000)
    p.add_argument('--tag', default=None, help='output name suffix (default: ion_<ion>_mask)')
    p.add_argument('--out', default=None, help='default: boidir')
    p.add_argument('--inject', type=float, nargs=2, default=None, metavar=('RATE', 'LAT'),
                   help='synthetic test: add RATE mm/yr along the heading (o > 0) to pixels north of LAT')
    return p.parse_args(iargs)


INJ = {}                                             # synthetic test: swath id -> (per-pixel rate [lines/yr], dt_p [yr])


def row(h, n, cor_min):
    """pair n of one swath: o (NaN where cor < cor_min), iota, cor. With --inject: o += rate * dt_p."""
    cor = h['cor'][n].astype(np.float32)
    o = h['o'][n].astype(np.float32); o[cor < cor_min] = np.nan
    if INJ:
        rate, dtp = INJ[h.attrs['swath']]
        o += (rate * dtp[n]).astype(np.float32)
    io = h['iota'][n].astype(np.float32) if h.attrs['has_ion'] else np.zeros_like(o)
    io[~np.isfinite(o)] = np.nan
    return o, io, cor


def bins_of(h, nbin):
    """bin id per pixel: overlap x range block (pixels stored per overlap, row-major, rows of equal width)."""
    ovl, lon_all = h['ovl'][:], h['lon'][:]
    lab = np.zeros(ovl.size, np.int64)
    nb = 0
    for v in np.unique(ovl):
        ii = np.where(ovl == v)[0]
        dl = np.diff(lon_all[ii])
        jump = np.where(dl > 0.5 * np.nanmax(np.abs(dl)))[0]          # range restarts at the next row
        w = (jump[0] + 1) if jump.size else ii.size
        lab[ii] = nb + (np.arange(ii.size) % w) // nbin
        nb += w // nbin + 1
    return lab


def main(iargs=None):
    a = get_args(iargs)
    tag = a.tag or f'ion_{a.ion}_mask'
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
    if a.inject:
        dtp = np.array([(datetime.strptime(q.split('_')[1], '%Y%m%d') - datetime.strptime(q.split('_')[0], '%Y%m%d')).days
                        for q in pairs]) / 365.25
        for h in H.values():
            INJ[h.attrs['swath']] = (np.where(h['lat'][:] > a.inject[1], a.inject[0] / 1e3 / h['dx_az'][:], 0.0), dtp)
        print(f'SYNTHETIC: +{a.inject[0]} mm/yr along heading north of {a.inject[1]} N', flush=True)
    P = (Ar[:, :, None] * Ar[:, None, :]).reshape(npair, m * m)
    D = np.diff(np.eye(nd), axis=0)[:, keep]
    R = D.T @ D * a.damp * np.abs(A).sum(0).mean()
    print(f'{npair} pairs, {nd} dates ({t[-1]:.2f} yr), ref {ref}, ion {a.ion}, cor >= {a.cor_min}, '
          f'gamma_t >= {a.gt_min}, kmad {a.kmad}, swaths {list(H)}', flush=True)

    # pass 1 (streamed per pair): per-pair scene medians of o and iota
    T0 = time.time()
    co = np.full(npair, np.nan); ci = np.full(npair, np.nan)
    for n in range(npair):
        r = [row(h, n, a.cor_min) for h in H.values()]
        oo = np.concatenate([x[0] for x in r]); ii_ = np.concatenate([x[1] for x in r])
        if np.isfinite(oo).any():
            co[n], ci[n] = np.nanmedian(oo), np.nanmedian(ii_)
    print(f'pass 1: pair constants ({time.time() - T0:.0f} s)', flush=True)

    # pass 2: ionosphere mask from pooled regression of (o - co) on (iota - ci) per bin
    SL, M = {}, {}
    for sw, h in H.items():
        npx = h['o'].shape[1]
        if a.ion == 'none':
            SL[sw] = np.full(npx, np.nan, np.float32); M[sw] = np.zeros(npx, bool); continue
        sxy = np.zeros(npx); sxx = np.zeros(npx)
        for n in range(npair):
            o, io, _ = row(h, n, a.cor_min)
            x = io - ci[n]; y = o - co[n]; ok = np.isfinite(x) & np.isfinite(y)
            sxy[ok] += x[ok] * y[ok]; sxx[ok] += x[ok] ** 2
        lab = bins_of(h, a.ion_bin)
        bxy = np.bincount(lab, sxy); bxx = np.bincount(lab, sxx)
        slope = np.where(bxx > 0, bxy / np.maximum(bxx, 1e-30), np.nan)[lab]
        SL[sw] = slope.astype(np.float32)
        M[sw] = np.abs(slope - 1) <= a.ion_tol
        print(f'IW{sw}: ion slope p10/p50/p90 {np.nanpercentile(slope, [10, 50, 90]).round(2)}, '
              f'M = 1 for {M[sw].mean():.2f} of pixels ({time.time() - T0:.0f} s)', flush=True)

    # pass 3: per-pair scene constants of b = o - M iota
    c = np.full(npair, np.nan)
    for n in range(npair):
        bb = []
        for sw, h in H.items():
            o, io, _ = row(h, n, a.cor_min)
            bb.append(o - np.where(M[sw], io, 0))
        bb = np.concatenate(bb)
        if np.isfinite(bb).any():
            c[n] = np.nanmedian(bb)
    print(f'pass 3: pair constants of b: std {np.nanstd(c):.5f} lines ({time.time() - T0:.0f} s)', flush=True)

    txt = []
    tb = t - t.mean(); stt = (tb ** 2).sum()

    def solve(w, bb):
        N = (w @ P).reshape(-1, m, m) + R
        x = np.linalg.solve(N, (bb @ Ar)[:, :, None])[:, :, 0]
        d = np.zeros((w.shape[0], nd)); d[:, keep] = x
        return d

    for sw, h in H.items():
        T0 = time.time()
        npx = h['o'].shape[1]
        Bs = np.empty((npair, npx), np.float32); csum = np.zeros(npx); cnum = np.zeros(npx)
        for n in range(npair):
            o, io, cr = row(h, n, a.cor_min)
            Bs[n] = o - np.where(M[sw], io, 0) - c[n]
            ok = np.isfinite(Bs[n]); csum[ok] += cr[ok]; cnum[ok] += 1
        W = np.isfinite(Bs)
        F = 2 * np.pi * DT * h['freq_hz'][:]                                       # rad per line
        V = np.full(npx, np.nan); S = np.full(npx, np.nan); G = np.full(npx, np.nan); NP = np.zeros(npx, int)
        idx = np.where(W.sum(0) >= a.min_pairs)[0]
        for k in range(0, idx.size, a.chunk):
            ii = idx[k:k + a.chunk]
            w = W[:, ii].T.astype(np.float64)
            bb = np.where(W[:, ii], Bs[:, ii], 0).T.astype(np.float64)
            d = solve(w, bb)
            r = np.where(w > 0, bb - d @ A.T, np.nan)
            med = np.nanmedian(r, 1, keepdims=True)
            mad = 1.4826 * np.nanmedian(np.abs(r - med), 1, keepdims=True)
            w = w * (np.abs(r - med) <= a.kmad * mad)
            bb = bb * w
            d = solve(w, bb)
            r = np.where(w > 0, bb - d @ A.T, np.nan)
            G[ii] = np.abs(np.nanmean(np.exp(1j * F[ii, None] * r), 1))
            NP[ii] = w.sum(1)
            v = (d - d.mean(1, keepdims=True)) @ tb / stt
            res = d - d.mean(1, keepdims=True) - v[:, None] * tb[None, :]
            V[ii] = v; S[ii] = res.std(1, ddof=2) / np.sqrt(stt)
        dx = h['dx_az'][:]
        vmm, smm = V * dx * 1e3, S * dx * 1e3
        good = np.isfinite(vmm) & (G >= a.gt_min) & (NP >= a.min_pairs)
        cor = np.where(cnum > 0, csum / np.maximum(cnum, 1), np.nan)
        del Bs, W
        vout = np.where(good, vmm, np.nan)
        with h5py.File(f'{out}/boi_vel_IW{sw}_{tag}.h5', 'w') as g:
            for k_, v_ in dict(lon=h['lon'][:], lat=h['lat'][:], v=vout.astype(np.float32),
                               v_all=vmm.astype(np.float32), sigma_v=smm.astype(np.float32),
                               gamma_t=G.astype(np.float32), n_pairs=NP.astype(np.int16), cor=cor.astype(np.float32),
                               ion_slope=SL[sw], ion_M=M[sw].astype(np.int8), ovl=h['ovl'][:],
                               dates=h['dates'][:]).items():
                g[k_] = v_
            g.attrs.update(ion=a.ion, cor_min=a.cor_min, gt_min=a.gt_min, kmad=a.kmad, min_pairs=a.min_pairs,
                           ion_bin=a.ion_bin, ion_tol=a.ion_tol, damp=a.damp, ref_date=ref,
                           units='mm/yr along track; sign [verify]')
        lat = h['lat'][:]
        txt.append(np.c_[h['lon'][:][good], lat[good], vmm[good], smm[good]])
        # scatter about 0.1-deg latitude bin medians; std of bin medians; N-S trend of bin medians
        lb = np.floor(lat[good] / 0.1).astype(int)
        u, inv = np.unique(lb, return_inverse=True)
        bm = np.array([np.median(vmm[good][inv == q]) for q in range(u.size)])
        bn = np.bincount(inv)
        scat = 1.4826 * np.median(np.abs(vmm[good] - bm[inv]))
        sel = bn >= 50
        tr = np.polyfit((u[sel] + 0.5) * 0.1, bm[sel], 1)[0] if sel.sum() > 2 else np.nan
        print(f'IW{sw}: kept {good.mean():.3f}  pixel scatter {scat:.1f}  std of 0.1-deg bin medians '
              f'{np.std(bm[sel]):.1f} mm/yr  N-S trend {tr:+.2f} mm/yr/deg  gamma_t p10/p50/p90 '
              f'{np.nanpercentile(G, [10, 50, 90]).round(2)}  n_pairs median {np.median(NP[good]):.0f}  '
              f'sigma_v median {np.median(smm[good]):.1f}  ({time.time() - T0:.0f} s)', flush=True)
    X = np.concatenate(txt)
    np.savetxt(f'{out}/boi_vel_{tag}.txt', X, fmt='%.5f %.5f %.3f %.3f',
               header=f'lon lat v sigma [mm/yr], BOI along-track, sign [verify], not plate-referenced; ion {a.ion}, '
                      f'cor>={a.cor_min}, gamma_t>={a.gt_min}, {len(dates)} dates {dates[0]}-{dates[-1]}')

    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, 3, figsize=(15, 8))
    lim = np.percentile(np.abs(X[:, 2] - np.median(X[:, 2])), 98)
    sc = axs[0].scatter(X[:, 0], X[:, 1], c=X[:, 2] - np.median(X[:, 2]), s=0.3, cmap='RdBu_r', vmin=-lim, vmax=lim,
                        rasterized=True)
    plt.colorbar(sc, ax=axs[0], shrink=0.6, label='BOI along-track velocity - median [mm/yr]')
    axs[0].set_title(f'v, kept pixels (ion {a.ion}, gamma_t >= {a.gt_min})', fontsize=9)
    for sw in H:
        with h5py.File(f'{out}/boi_vel_IW{sw}_{tag}.h5', 'r') as g:
            s1 = axs[1].scatter(g['lon'][:], g['lat'][:], c=g['gamma_t'][:], s=0.2, vmin=0.3, vmax=1, rasterized=True)
            s2 = axs[2].scatter(g['lon'][:], g['lat'][:], c=g['ion_slope'][:], s=0.2, vmin=-0.5, vmax=2.5,
                                cmap='coolwarm', rasterized=True)
    plt.colorbar(s1, ax=axs[1], shrink=0.6, label='temporal coherence gamma_t')
    plt.colorbar(s2, ax=axs[2], shrink=0.6, label='ionosphere slope a (1 = T explains o)')
    axs[1].set_title('gamma_t', fontsize=9); axs[2].set_title(f'ion slope, M = |a - 1| <= {a.ion_tol}', fontsize=9)
    for ax in axs:
        ax.set_aspect(1 / np.cos(np.radians(np.median(X[:, 1])))); ax.set_xlabel('lon')
    fig.savefig(f'{out}/pic/boi_vel_{tag}.png', dpi=170, bbox_inches='tight')
    print('wrote', f'{out}/boi_vel_IW*_{tag}.h5', f'{out}/boi_vel_{tag}.txt', f'{out}/pic/boi_vel_{tag}.png')


if __name__ == '__main__':
    main()
