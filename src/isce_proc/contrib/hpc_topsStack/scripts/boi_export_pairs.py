#!/usr/bin/env python
"""Burst-overlap interferometry (BOI): export the pair-level observations (all swaths in one file) for selection and
time-series inversion elsewhere (e.g. marmot).

Input: <boidir>/boi_pairs_IW<n>.h5 (boi_pairs.py).
Output: <out>.h5 with
  o        (npair, npix) float32  pair offset Phi_p / F [single-look azimuth lines]; o > 0 = along the satellite heading.
                                  Velocity toward the north-positive direction_az: v = sign * o * dx_az / dt.
  cor      (npair, npix) float16  mean ESD coherence (5x5 phase-only, biased high; Li 2021 uses >= 0.45)
  iota     (npair, npix) float32  only if the stack has ion_azshift_dates (-(T_j - T_i), lines)
  pairs, dates, ref_date, pair_dt_days, pair_median (scene median of o over cor >= 0.45, all swaths: the per-pair
  constant that boi_ts.py removes), lon, lat, swath, ovl, dx_az [m/line], freq_hz [Hz]
  attrs: heading_deg, direction_az_deg, sign (+1 asc, -1 dsc), looks, source
Chunks (32 pairs x 32768 pixels), gzip 4 + shuffle: both pair rows and pixel blocks read efficiently.
Usage: boi_export_pairs.py <boidir> <out.h5> [--cor-min 0.45]
"""
import os, sys, glob, time, argparse
import numpy as np
import h5py

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boi_export import heading_of


def main(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('boidir'); p.add_argument('out'); p.add_argument('--cor-min', type=float, default=0.45)
    a = p.parse_args(iargs)
    files = sorted(glob.glob(f'{a.boidir}/boi_pairs_IW?.h5'))
    H = [h5py.File(f, 'r') for f in files]
    h0 = H[0]
    pairs = h0['pairs'][:]; dates = h0['dates'][:]; npair = len(pairs)
    has_ion = all(int(h.attrs['has_ion']) for h in H)
    cat = lambda k: np.concatenate([h[k][:] for h in H])
    lon, lat, ovl = cat('lon'), cat('lat'), cat('ovl')
    swath = np.concatenate([np.full(h['lon'].shape, int(h.attrs['swath']), np.int8) for h in H])
    hs = [heading_of(lon[swath == s], lat[swath == s], ovl[swath == s]) for s in np.unique(swath)]
    head = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(hs))))) % 360
    desc = 90 < head < 270
    npix = lon.size
    from datetime import datetime
    ps = pairs.astype(str)
    dtd = np.array([(datetime.strptime(q[9:], '%Y%m%d') - datetime.strptime(q[:8], '%Y%m%d')).days for q in ps])
    print(f'{len(files)} swaths, {npair} pairs, {len(dates)} dates, {npix} pixels; heading {head:.1f}; ion {has_ion}')
    T0 = time.time()
    with h5py.File(a.out, 'w') as g:
        kw = dict(chunks=(min(32, npair), min(32768, npix)), compression='gzip', compression_opts=4, shuffle=True)
        do = g.create_dataset('o', (npair, npix), np.float32, fillvalue=np.nan, **kw)
        dc = g.create_dataset('cor', (npair, npix), np.float16, **kw)
        di = g.create_dataset('iota', (npair, npix), np.float32, fillvalue=np.nan, **kw) if has_ion else None
        med = np.full(npair, np.nan)
        B = 32
        for n0 in range(0, npair, B):
            n1 = min(npair, n0 + B)
            O = np.stack([np.concatenate([h['o'][n] for h in H]) for n in range(n0, n1)])
            C = np.stack([np.concatenate([h['cor'][n] for h in H]) for n in range(n0, n1)])
            do[n0:n1] = O; dc[n0:n1] = C
            if di is not None:
                di[n0:n1] = np.stack([np.concatenate([h['iota'][n] for h in H]) for n in range(n0, n1)])
            for k in range(n1 - n0):
                m = np.isfinite(O[k]) & (C[k].astype(np.float32) >= a.cor_min)
                if m.any():
                    med[n0 + k] = np.median(O[k][m])
            if n0 % 640 == 0:
                print(f'  pairs {n1}/{npair} ({time.time() - T0:.0f} s)', flush=True)
        for k, v in dict(pairs=pairs, dates=dates, pair_dt_days=dtd.astype(np.int16), pair_median=med.astype(np.float32),
                         lon=lon, lat=lat, swath=swath, ovl=ovl, dx_az=cat('dx_az'), freq_hz=cat('freq_hz')).items():
            g[k] = v
        g.attrs.update(ref_date=str(h0.attrs['ref_date']), heading_deg=head, direction_az_deg=(head + 180) % 360 if desc else head,
                       sign=-1.0 if desc else 1.0, looks=str(h0.attrs.get('single_look_looks', '')),
                       source=os.path.abspath(a.boidir), cor_min_for_pair_median=a.cor_min,
                       notes='o [lines], o > 0 along heading. v_north_positive [mm/yr] = sign * o * dx_az * 1e3 / '
                             '(pair_dt_days / 365.25) for a single pair; for time series remove pair_median (or '
                             'your own per-pair scene constant) and invert with A (+1 at secondary, -1 at reference).')
    print(f'wrote {a.out} {os.path.getsize(a.out) / 1e9:.1f} GB ({time.time() - T0:.0f} s)')


if __name__ == '__main__':
    main()
