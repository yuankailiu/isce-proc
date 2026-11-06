#!/usr/bin/env python
"""BOI check 1b: is freq_<bb>.bin (overlap_withDEM.py) the right phase-per-line factor?

For one secondary date d (stack reference R, S_R = 0), per swath and overlap:
  DDg = (r1 g1*) (r2 g2*)*   with g = geometry-only overlap SLC (run 06)
  DDf = (r1 f1*) (r2 f2*)*   with f = fine-coregistered burst (run 10, azimuth shifted by S_d)
  dphi = angle(DDg DDf*)  = true phase response to the applied shift S_d
  ratio = dphi / (freq * S_d), freq from overlapSpectralSeparation(R, d) * 2 pi dt
ratio ~ +-1 -> freq right; ~ +-0.5 -> freq is 2x too large.
"""
import os, sys, glob
import numpy as np
TOPS = os.path.expanduser('~/tools/isce2/src/isce2/contrib/stack/topsStack')
sys.path.insert(0, TOPS)
import isce  # noqa
from osgeo import gdal
import s1a_isce_utils as ut
from overlap_withDEM import overlapSpectralSeparation

stack, date = sys.argv[1], sys.argv[2]
swaths = [int(s) for s in sys.argv[3].split(',')] if len(sys.argv) > 3 else [1, 2, 3]
AL, RL = 5, 15
S = float(open(f'{stack}/misreg/azimuth/dates/{date}.txt').read().split()[0])
print(f'date {date}  S_d = {S:+.6f} lines')


def rd(path, y0=None, ny=None):
    ds = gdal.Open(path if os.path.exists(path) else path + '.vrt')
    if y0 is None:
        return ds.ReadAsArray()
    return ds.GetRasterBand(1).ReadAsArray(0, y0, ds.RasterXSize, ny)


def look(a):
    ny, nx = (a.shape[0] // AL) * AL, (a.shape[1] // RL) * RL
    return a[:ny, :nx].reshape(ny // AL, AL, nx // RL, RL).sum(axis=(1, 3))


rows = []
for sw in swaths:
    IW = f'IW{sw}'
    rT = ut.loadProduct(f'{stack}/reference/overlap/{IW}_top.xml')
    rB = ut.loadProduct(f'{stack}/reference/overlap/{IW}_bottom.xml')
    sT = ut.loadProduct(f'{stack}/coreg_secondarys/{date}/overlap/{IW}_top.xml')
    sB = ut.loadProduct(f'{stack}/coreg_secondarys/{date}/overlap/{IW}_bottom.xml')
    rFull = ut.loadProduct(f'{stack}/reference/{IW}.xml')
    fFull = ut.loadProduct(f'{stack}/coreg_secondarys/{date}/{IW}.xml')
    minR, minS = rT.bursts[0].burstNumber, sT.bursts[0].burstNumber
    nb0 = rFull.bursts[0].burstNumber
    nf0 = fFull.bursts[0].burstNumber
    roff = f'{stack}/reference/overlap/{IW}'
    soff = f'{stack}/coreg_secondarys/{date}/overlap/{IW}'
    for ind, ob in enumerate(rT.bursts):
        ii = ob.burstNumber
        if ii < minS or ii + 1 > sT.bursts[-1].burstNumber + 1:
            continue
        sind = ii - minS
        if sind >= len(sT.bursts) or sT.bursts[sind].burstNumber != ii:
            continue
        obB = rB.bursts[ind]
        mFT, mFB = rT.source.bursts[ii - minR], rB.source.bursts[ii - minR + 1]
        sFT, sFB = sT.source.bursts[sind], sB.source.bursts[sind + 1]
        tag = f'{ii:02d}_{ii+1:02d}'
        fq = overlapSpectralSeparation(ob, obB, mFT, mFB, sFT, sFB,
                                       f'{roff}/azimuth_top_{tag}.off', f'{roff}/range_top_{tag}.off',
                                       f'{roff}/azimuth_bot_{tag}.off', f'{roff}/range_bot_{tag}.off',
                                       f'{soff}/azimuth_top_{tag}.off', f'{soff}/range_top_{tag}.off',
                                       f'{soff}/azimuth_bot_{tag}.off', f'{soff}/range_bot_{tag}.off')
        dt = mFT.azimuthTimeInterval
        freq = fq * 2 * np.pi * dt                                 # rad per line, as in freq_<bb>.bin
        # overlap SLCs
        r1 = rd(ob.image.filename); r2 = rd(obB.image.filename)
        g1 = rd(f'{soff}/burst_top_{tag}.slc'); g2 = rd(f'{soff}/burst_bot_{tag}.slc')
        # same lines from full bursts (reference grid)
        bT, bB = rFull.bursts[ii - nb0], rFull.bursts[ii + 1 - nb0]
        yT = int(round((ob.sensingStart - bT.sensingStart).total_seconds() / dt))
        yB = int(round((obB.sensingStart - bB.sensingStart).total_seconds() / dt))
        n = ob.numberOfLines
        R1 = rd(f'{stack}/reference/{IW}/burst_{ii:02d}.slc', yT, n)
        R2 = rd(f'{stack}/reference/{IW}/burst_{ii+1:02d}.slc', yB, n)
        f1 = rd(f'{stack}/coreg_secondarys/{date}/{IW}/burst_{ii:02d}.slc', yT, n)
        f2 = rd(f'{stack}/coreg_secondarys/{date}/{IW}/burst_{ii+1:02d}.slc', yB, n)
        same_ref = np.nanmax(np.abs(R1 - r1)) if R1.shape == r1.shape else np.nan
        DDg = (r1 * np.conj(g1)) * np.conj(r2 * np.conj(g2))
        DDf = (r1 * np.conj(f1)) * np.conj(r2 * np.conj(f2))
        Zd = look(DDg * np.conj(DDf))
        Zg, Zf = look(DDg), look(DDf)
        cnt = look(((np.abs(DDg) > 0) & (np.abs(DDf) > 0)).astype(np.complex64)).real
        F = look(freq.astype(np.complex64)).real / (AL * RL)
        ok = (cnt == AL * RL) & (np.abs(Zd) > 0)
        cohg = np.abs(Zg) / np.maximum(look(np.abs(DDg).astype(np.complex64)).real, 1e-30)
        dphi = np.angle(Zd[ok])
        pred = F[ok] * S
        ratio = np.median(dphi / pred)
        hc = ok & (cohg > 0.85)
        res_g = np.median(np.angle(Zg[hc])) if hc.any() else np.nan
        res_f = np.median(np.angle(Zf[hc])) if hc.any() else np.nan
        rows.append((sw, ii, np.median(F[ok]) / (2 * np.pi * dt), np.median(dphi), np.median(pred), ratio,
                     np.std(dphi / pred), res_g, res_f, int(hc.sum()), same_ref, yT, yB))
        print(f'{IW} ovl {tag}: freq/(2pi dt) {rows[-1][2]:8.0f} Hz  dphi {rows[-1][3]:+.4f}  '
              f'freq*S {rows[-1][4]:+.4f}  ratio {ratio:+.3f} (std {rows[-1][6]:.3f})  '
              f'med angle DDg {res_g:+.4f} DDf {res_f:+.4f} (n_hc {rows[-1][9]})  |R-r| {same_ref:.2g}', flush=True)

a = np.array([r[5] for r in rows])
print(f'ALL: ratio median {np.median(a):+.3f}, mean {a.mean():+.3f}, std {a.std():.3f}, n {len(a)}')
