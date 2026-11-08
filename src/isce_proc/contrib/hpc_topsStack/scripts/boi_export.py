#!/usr/bin/env python
"""Burst-overlap interferometry (BOI): export per-pixel results, geocoded, for analysis elsewhere (e.g. marmot).

Input: <boidir>/boi_vel_IW<n>_<tag>.h5 from boi_ts.py, one or more tags (e.g. ion_none_mask ion_T_mask).
Velocities are converted to the north-positive along-track direction: heading (ascending) or heading + 180 deg
(descending), as Li et al. (2024) (dsc ~N12E, asc ~N13W). Sign verified on d021 (synthetic -9.96 vs -10 mm/yr;
DST left-lateral east-west difference positive).

Output in <outdir>:
  <name>_pixels.h5   all BOI pixels (kept or not): lon lat swath ovl dx_az freq_hz cor n_pairs, and per tag:
                     v_<tag> (NaN where masked), v_all_<tag> (no mask), sigma_<tag>, gamma_t_<tag>, ion_slope/ion_M
                     (ion tags); attrs: direction_az, heading, dates, tags, notes
  <name>_v_<tag>.tif geocoded grid (EPSG:4326, --posting deg, mean of kept pixels per cell; band 1 v, band 2 sigma
                     of the cell mean, band 3 n pixels), NaN outside the overlap strips
Usage: boi_export.py <boidir> <outdir> --name d021_test --tags ion_none_mask ion_T_mask [--posting 0.0015]
"""
import os, glob, argparse
import numpy as np
import h5py
from osgeo import gdal, osr


def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(lon2 - lon1)
    return np.degrees(np.arctan2(np.sin(dl) * np.cos(p2), np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl))) % 360


def heading_of(lo, la, ov):
    hd = []
    for o in np.unique(ov)[::5]:
        ii = np.where(ov == o)[0]; dl = np.diff(lo[ii])
        jump = np.where(dl > 0.5 * np.nanmax(np.abs(dl)))[0]
        if jump.size < 2:
            continue
        w = jump[0] + 1; nr = ii.size // w; c = w // 2
        hd.append(bearing(la[ii[c]], lo[ii[c]], la[ii[(nr - 1) * w + c]], lo[ii[(nr - 1) * w + c]]))
    return np.degrees(np.angle(np.mean(np.exp(1j * np.radians(hd))))) % 360


def main(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('boidir'); p.add_argument('outdir')
    p.add_argument('--name', required=True); p.add_argument('--tags', nargs='+', required=True)
    p.add_argument('--posting', type=float, default=0.0015)
    a = p.parse_args(iargs)
    os.makedirs(a.outdir, exist_ok=True)
    sws = sorted(int(f[-4]) for f in glob.glob(f'{a.boidir}/boi_pairs_IW?.h5'))
    base = {}
    for sw in sws:
        with h5py.File(f'{a.boidir}/boi_pairs_IW{sw}.h5', 'r') as h:
            base.setdefault('lon', []).append(h['lon'][:]); base.setdefault('lat', []).append(h['lat'][:])
            base.setdefault('ovl', []).append(h['ovl'][:]); base.setdefault('dx_az', []).append(h['dx_az'][:])
            base.setdefault('freq_hz', []).append(h['freq_hz'][:])
            base.setdefault('swath', []).append(np.full(h['lon'].shape, sw, np.int8))
            dates = h['dates'][:]; ref = h.attrs['ref_date']
    base = {k: np.concatenate(v) for k, v in base.items()}
    hs = [heading_of(base['lon'][base['swath'] == sw], base['lat'][base['swath'] == sw], base['ovl'][base['swath'] == sw])
          for sw in sws]                                      # per swath: overlap ids repeat across swaths
    head = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(hs))))) % 360
    assert max(abs(((h_ - head + 180) % 360) - 180) for h_ in hs) < 5, f'inconsistent swath headings {hs}'
    desc = 90 < head < 270
    outdir_az = (head + 180) % 360 if desc else head
    sgn = -1.0 if desc else 1.0
    print(f'heading {head:.1f} deg -> output direction {outdir_az:.1f} deg (sign {sgn:+.0f}); {base["lon"].size} pixels')

    fout = f'{a.outdir}/{a.name}_pixels.h5'
    with h5py.File(fout, 'w') as g:
        for k, v in base.items():
            g[k] = v
        for tag in a.tags:
            d = {}
            for sw in sws:
                with h5py.File(f'{a.boidir}/boi_vel_IW{sw}_{tag}.h5', 'r') as h:
                    for k in ('v', 'v_all', 'sigma_v', 'gamma_t', 'n_pairs', 'cor', 'ion_slope', 'ion_M'):
                        if k in h:
                            d.setdefault(k, []).append(h[k][:])
                    attrs = dict(h.attrs)
            d = {k: np.concatenate(v) for k, v in d.items()}
            g[f'v_{tag}'] = (sgn * d['v']).astype(np.float32)
            g[f'v_all_{tag}'] = (sgn * d['v_all']).astype(np.float32)
            g[f'sigma_{tag}'] = d['sigma_v'].astype(np.float32)
            g[f'gamma_t_{tag}'] = d['gamma_t'].astype(np.float32)
            if 'cor' not in g:
                g['cor'] = d['cor'].astype(np.float32); g['n_pairs'] = d['n_pairs']
            if np.isfinite(d['ion_slope']).any():
                g[f'ion_slope_{tag}'] = d['ion_slope']; g[f'ion_M_{tag}'] = d['ion_M']
            g[f'v_{tag}'].attrs.update({k: str(v) for k, v in attrs.items()})
            ok = np.isfinite(d['v'])
            print(f'{tag}: kept {ok.sum()} of {ok.size} pixels; v (dir {outdir_az:.0f}) median {np.median(sgn * d["v"][ok]):+.2f} mm/yr')
            # geocoded grid
            lo, la, vv, ss = base['lon'][ok], base['lat'][ok], sgn * d['v'][ok], d['sigma_v'][ok]
            x0, y1 = np.floor(lo.min() / a.posting) * a.posting, np.ceil(la.max() / a.posting) * a.posting
            nx = int(np.ceil((lo.max() - x0) / a.posting)) + 1; ny = int(np.ceil((y1 - la.min()) / a.posting)) + 1
            ix = ((lo - x0) / a.posting).astype(np.int64); iy = ((y1 - la) / a.posting).astype(np.int64)
            k = iy * nx + ix
            n = np.bincount(k, minlength=nx * ny).astype(np.float32)
            sv = np.bincount(k, vv, minlength=nx * ny); sv2 = np.bincount(k, vv ** 2, minlength=nx * ny)
            with np.errstate(invalid='ignore', divide='ignore'):
                m = np.where(n > 0, sv / n, np.nan)
                sd = np.where(n > 1, np.sqrt(np.maximum(sv2 / n - m ** 2, 0) / n), np.nan)
            tif = f'{a.outdir}/{a.name}_v_{tag}.tif'
            ds = gdal.GetDriverByName('GTiff').Create(tif, nx, ny, 3, gdal.GDT_Float32,
                                                      options=['COMPRESS=DEFLATE', 'TILED=YES'])
            ds.SetGeoTransform((x0, a.posting, 0, y1, 0, -a.posting))
            sr = osr.SpatialReference(); sr.ImportFromEPSG(4326); ds.SetProjection(sr.ExportToWkt())
            for b, arr in enumerate((m, sd, n), 1):
                band = ds.GetRasterBand(b); band.WriteArray(arr.reshape(ny, nx).astype(np.float32))
                band.SetNoDataValue(np.nan if b < 3 else 0)
                band.SetDescription(['v_mm_per_yr', 'sigma_of_cell_mean', 'n_pixels'][b - 1])
            ds.SetMetadata({'direction_az_deg': f'{outdir_az:.2f}', 'heading_deg': f'{head:.2f}', 'tag': tag,
                            'units': 'mm/yr along track, positive toward direction_az; not plate-referenced'})
            ds = None
            print(f'   wrote {tif} ({nx} x {ny}, posting {a.posting} deg, cells with data {(n > 0).sum()})')
        g.attrs.update(direction_az_deg=outdir_az, heading_deg=head, ref_date=ref, dates=dates, tags=a.tags,
                       source=os.path.abspath(a.boidir),
                       notes='v [mm/yr] along track, positive toward direction_az (north-positive), not '
                             'plate-referenced (one constant per pair removed over the scene). v_<tag> NaN where '
                             'masked (cor<0.45 obs, outlier pairs, gamma_t, n_pairs); v_all_<tag> unmasked.')
    print('wrote', fout, f'{os.path.getsize(fout) / 1e6:.0f} MB')


if __name__ == '__main__':
    main()
