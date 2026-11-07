#!/usr/bin/env python
"""BOI check 2 (set PEROVL=1 for per-overlap medians): sign/scale of ion_azshift_dates (T) vs ESD overlap offsets.

Pair p = (i, j). Per overlap pixel (5x15 looks), single-look lines:
  o_p  = angle(ESD overlap .int) / (freq / k)    k = 3 if i is the stack reference else 4 (check 1b)
  dT   = T_j - T_i sampled at the pixel (ion grid = merged full grid / (28 az, 96 rg))
Both demeaned per swath (ESD-like median). Fit o_p = a * dT (+ c): a ~ +1 or -1.
"""
import os, sys
import numpy as np
TOPS = os.path.expanduser('~/tools/isce2/src/isce2/contrib/stack/topsStack')
sys.path.insert(0, TOPS)
import isce  # noqa
from osgeo import gdal
from scipy.ndimage import map_coordinates
import s1a_isce_utils as ut
gdal.UseExceptions()

st, pair = sys.argv[1], sys.argv[2]
cmin = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
i, j = pair.split('_')
AL, RL, IAZ, IRG = 5, 15, 28, 96
ref_date = sorted(os.listdir(f'{st}/misreg/azimuth/dates'))[0][:8]
for d in sorted(os.listdir(f'{st}/misreg/azimuth/dates')):
    if float(open(f'{st}/misreg/azimuth/dates/{d}').read().split()[0]) == 0.0:
        ref_date = d[:8]
k = 3.0 if i == ref_date else 4.0
rd = lambda p: gdal.Open(p + '.vrt' if os.path.exists(p + '.vrt') else p).ReadAsArray()
Ti, Tj = rd(f'{st}/ion_azshift_dates/{i}.ion'), rd(f'{st}/ion_azshift_dates/{j}.ion')
dTg = (Tj - Ti).astype(np.float64)

full = {sw: ut.loadProduct(f'{st}/reference/IW{sw}.xml') for sw in (1, 2, 3)}
t0 = min(p.bursts[0].sensingStart for p in full.values())
r0 = min(p.bursts[0].startingRange for p in full.values())
dt = full[1].bursts[0].azimuthTimeInterval
dr = full[1].bursts[0].rangePixelSize
print(f'pair {pair}  stack ref {ref_date}  k {k:.0f}  coh > {cmin}')

allo, alld = [], []
for sw in (1, 2, 3):
    ovl = ut.loadProduct(f'{st}/reference/overlap/IW{sw}_top.xml')
    O, D = [], []
    for ob in ovl.bursts:
        ii = ob.burstNumber
        base = f'{st}/ESD/{pair}/IW{sw}/overlap_{ii:02d}.5alks_15rlks'
        if not os.path.exists(base + '.int'):
            continue
        z = rd(base + '.int'); c = rd(base + '.cor')
        f = rd(f'{st}/ESD/{pair}/IW{sw}/freq_{ii:02d}.5alks_15rlks.bin')
        if f.ndim == 3:
            f = f[0]
        f = f / (AL * RL) if np.nanmedian(np.abs(f)) > 1000 else f   # multilook() may sum
        ny, nx = z.shape
        yy = (np.arange(ny) * AL + (AL - 1) / 2)[:, None] * np.ones((1, nx))
        xx = np.ones((ny, 1)) * (np.arange(nx) * RL + (RL - 1) / 2)[None, :]
        my = (ob.sensingStart - t0).total_seconds() / dt + yy        # merged full line
        mx = (ob.startingRange - r0) / dr + xx                     # merged full sample
        gy, gx = (my - (IAZ - 1) / 2) / IAZ, (mx - (IRG - 1) / 2) / IRG
        dT = map_coordinates(dTg, [gy, gx], order=1, mode='nearest')
        ok = (c > cmin) & (np.abs(z) > 0) & (f != 0) & np.isfinite(dT)
        o = np.angle(z) / (f / k)
        O.append(o[ok]); D.append(dT[ok])
        if os.environ.get("PEROVL") and ok.sum() > 200: print(f"  IW{sw} ovl {ii:02d}: n {ok.sum():6d}  med o {np.median(o[ok]):+.4f}  med dT {np.median(dT[ok]):+.4f}")
    if not O:
        continue
    O, D = np.concatenate(O), np.concatenate(D)
    O -= np.median(O); D -= np.median(D)
    A = np.vstack([D, np.ones_like(D)]).T
    a, cst = np.linalg.lstsq(A, O, rcond=None)[0]
    # binned (robust) slope: medians of O in dT deciles
    q = np.quantile(D, np.linspace(0, 1, 11))
    bm = [(np.median(D[(D >= q[n]) & (D < q[n+1])]), np.median(O[(D >= q[n]) & (D < q[n+1])])) for n in range(10)]
    bx, by = np.array(bm).T
    ab = np.polyfit(bx, by, 1)[0]
    r = np.corrcoef(O, D)[0, 1]
    lo, hi = np.percentile(D, [5, 95]); t = (D >= lo) & (D <= hi)
    at = np.polyfit(D[t], O[t], 1)[0]
    bs = []
    rng = np.random.default_rng(0)
    for _ in range(50):
        s_ = rng.integers(0, t.sum(), t.sum()); bs.append(np.polyfit(D[t][s_], O[t][s_], 1)[0])
    print(f'IW{sw}: trimmed 5-95% slope {at:+.3f} +- {np.std(bs):.3f}')
    print(f'IW{sw}: n {O.size:7d}  std o {O.std():.4f}  std dT {D.std():.4f} lines  '
          f'LSQ slope {a:+.3f}  binned slope {ab:+.3f}  corr {r:+.3f}')
    print('      deciles dT:', ' '.join(f'{v:+.4f}' for v in bx))
    print('      median o  :', ' '.join(f'{v:+.4f}' for v in by))
    allo.append(O); alld.append(D)
O, D = np.concatenate(allo), np.concatenate(alld)
print(f'ALL: LSQ slope {np.polyfit(D, O, 1)[0]:+.3f}, corr {np.corrcoef(O, D)[0,1]:+.3f}, n {O.size}')
