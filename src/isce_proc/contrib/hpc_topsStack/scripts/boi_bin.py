#!/usr/bin/env python
"""Burst-overlap interferometry (BOI), step 3: binned along-track velocity.

Input: <boidir>/boi_vel_IW<n>_<tag>.h5 (boi_ts.py; v [mm/yr] NaN where masked).
Sign: v > 0 = displacement along the satellite heading. Derivation: geo2rdr samples the secondary at
sensingStart - S*dt, i.e. S lines later; check 1b: applying +S removes the ESD phase, so o > 0 means the target
is imaged later in the later date, i.e. moved along the flight direction (azimuth time increases along heading).
Output direction: heading (ascending) or heading + 180 deg (descending), so that positive points roughly north,
as Li et al. (2024) (dsc N12E, asc N13W). The direction azimuth is computed from the geometry and written.

Per cell (--cell deg): median of the kept pixels, n, sigma = 1.4826 MAD / sqrt(n); cells with n < --min-n dropped.
Noise check: std of differences between adjacent cells / sqrt(2) vs median sigma.
Output: <boidir>/boi_bin_<tag>_<cell>.txt (lon lat v sigma n; v along the output direction, NOT plate-referenced)
        <boidir>/pic/boi_bin_<tag>_<cell>.png (map + latitude profile)
Usage: boi_bin.py <boidir> [--tag ion_T_mask] [--cell 0.02] [--min-n 20]
"""
import glob, argparse
import numpy as np
import h5py


def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(lon2 - lon1)
    return np.degrees(np.arctan2(np.sin(dl) * np.cos(p2), np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl))) % 360


def main(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('boidir'); p.add_argument('--tag', default='ion_T_mask')
    p.add_argument('--cell', type=float, default=0.02); p.add_argument('--min-n', type=int, default=20)
    a = p.parse_args(iargs)
    lon, lat, v, hd = [], [], [], []
    for f in sorted(glob.glob(f'{a.boidir}/boi_vel_IW?_{a.tag}.h5')):
        with h5py.File(f, 'r') as h:
            lo, la, vv, ov = h['lon'][:], h['lat'][:], h['v'][:], h['ovl'][:]
        # heading: per overlap, first vs last row (rows of equal width; range restarts at each row)
        for o in np.unique(ov)[::5]:
            ii = np.where(ov == o)[0]; dl = np.diff(lo[ii])
            jump = np.where(dl > 0.5 * np.nanmax(np.abs(dl)))[0]
            if jump.size < 2:
                continue
            w = jump[0] + 1; nr = ii.size // w; c = w // 2
            k0, k1 = ii[c], ii[(nr - 1) * w + c]                   # same column, first and last row
            hd.append(bearing(la[k0], lo[k0], la[k1], lo[k1]))
        ok = np.isfinite(vv)
        lon.append(lo[ok]); lat.append(la[ok]); v.append(vv[ok])
    lon, lat, v = map(np.concatenate, (lon, lat, v))
    head = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(hd))))) % 360
    desc = 90 < head < 270
    outdir = (head + 180) % 360 if desc else head
    sgn = -1.0 if desc else 1.0
    vo = sgn * v
    print(f'heading {head:.1f} deg ({"descending" if desc else "ascending"}); output direction azimuth {outdir:.1f} deg '
          f'(positive toward N{outdir:.0f}E)' if outdir < 180 else f'heading {head:.1f}; output direction {outdir:.1f} deg')

    cy = np.floor(lat / a.cell).astype(np.int64); cx = np.floor(lon / a.cell).astype(np.int64)
    key = cy * 100000 + cx
    u, inv = np.unique(key, return_inverse=True)
    order = np.argsort(inv); bounds = np.r_[0, np.cumsum(np.bincount(inv))]
    rows = []
    for q in range(u.size):
        s = order[bounds[q]:bounds[q + 1]]
        if s.size < a.min_n:
            continue
        m = np.median(vo[s]); sig = 1.4826 * np.median(np.abs(vo[s] - m)) / np.sqrt(s.size)
        rows.append((np.median(lon[s]), np.median(lat[s]), m, sig, s.size, cy[s[0]], cx[s[0]]))
    R = np.array(rows)
    # adjacent-cell differences (same row of cells, next column; or next row same column)
    idx = {(int(r[5]), int(r[6])): k for k, r in enumerate(R)}
    dd = [R[k, 2] - R[idx[(int(r[5]), int(r[6]) + 1)], 2] for k, r in enumerate(R) if (int(r[5]), int(r[6]) + 1) in idx]
    dd = np.array(dd)
    print(f'cells {len(R)} (cell {a.cell} deg, n >= {a.min_n}); n median {np.median(R[:, 4]):.0f}; '
          f'sigma median {np.median(R[:, 3]):.2f} mm/yr; adjacent-cell difference std/sqrt2 '
          f'{1.4826 * np.median(np.abs(dd - np.median(dd))) / np.sqrt(2):.2f} mm/yr ({dd.size} pairs); '
          f'v p2/p50/p98 {np.percentile(R[:, 2], [2, 50, 98]).round(1)}')
    out = f'{a.boidir}/boi_bin_{a.tag}_{a.cell:g}'
    np.savetxt(out + '.txt', R[:, :5], fmt='%.5f %.5f %.3f %.3f %d',
               header=f'lon lat v sigma n; BOI along-track velocity [mm/yr] positive toward azimuth {outdir:.1f} deg '
                      f'(heading {head:.1f}); median of kept pixels per {a.cell} deg cell; not plate-referenced; tag {a.tag}')
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 8), gridspec_kw=dict(width_ratios=[1, 1]))
    vm = R[:, 2] - np.median(R[:, 2]); lim = np.percentile(np.abs(vm), 98)
    sc = ax[0].scatter(R[:, 0], R[:, 1], c=vm, s=6, marker='s', cmap='RdBu_r', vmin=-lim, vmax=lim)
    plt.colorbar(sc, ax=ax[0], shrink=0.6, label=f'v - median [mm/yr], + toward {outdir:.0f} deg')
    ax[0].set_aspect(1 / np.cos(np.radians(np.median(R[:, 1])))); ax[0].set_xlabel('lon'); ax[0].set_ylabel('lat')
    ax[0].set_title(f'BOI binned {a.cell} deg ({a.tag})', fontsize=9)
    ax[1].errorbar(vm, R[:, 1], xerr=R[:, 3], fmt='.', ms=2, lw=0.3, alpha=0.6)
    ax[1].set_xlabel('v - median [mm/yr]'); ax[1].set_ylabel('lat'); ax[1].set_xlim(-2 * lim, 2 * lim)
    ax[1].set_title('cells vs latitude', fontsize=9)
    fig.savefig(f'{a.boidir}/pic/boi_bin_{a.tag}_{a.cell:g}.png', dpi=170, bbox_inches='tight')
    print('wrote', out + '.txt', f'{a.boidir}/pic/boi_bin_{a.tag}_{a.cell:g}.png')


if __name__ == '__main__':
    main()
