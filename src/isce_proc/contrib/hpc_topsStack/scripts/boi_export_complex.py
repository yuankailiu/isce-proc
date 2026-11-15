#!/usr/bin/env python
"""Burst-overlap interferometry (BOI): export complex pair observations Phi_p for a latitude band, on exactly the pixels
of boi_pairs.py / boi_export_pairs.py (same looks, same order), for complex filtering tests.

Per pair, swath, overlap (as boi_pairs.do_pair): z = ESD overlap_<bb>.5alks_15rlks.int (5 x 15 looks); complex sum over
the extra looks (default 2 x 4); coh = |sum exp(i phi)| / N (phase-only); pixels with < 50 % valid looks -> 0.
  phi_c = exp(i angle(sum z)) * coh      (complex64)
so angle(phi_c) / F = o of boi_pairs (checked on the first pairs against <pairs.h5>).
Output <out.h5>: phi (npair, nsel) complex64 (gzip 4 + shuffle), pix_index (index into the full pixel list of
<pairs.h5>), lon, lat, swath, ovl, freq_rad_per_line, pairs, dates.
Usage: boi_export_complex.py <stack> <pairs.h5> <out.h5> --lat 31.8 33.0 [--nproc 3]
"""
import os, sys, time, argparse
import numpy as np
import h5py
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TOPS = os.path.expanduser('~/tools/isce2/src/isce2/contrib/stack/topsStack')
sys.path.insert(0, TOPS)
import boi_pairs as bp

_S = {}


def pair_phi(p):
    st, G, la, lr, use = _S['stack'], _S['G'], _S['la'], _S['lr'], _S['use']
    out = []
    for sw, ovl in G.items():
        for k, o in enumerate(ovl):
            npx = o['lat'].size
            if not use[sw][k]:
                continue
            base = f'{st}/ESD/{p}/IW{sw}/overlap_{o["ii"]:02d}.5alks_15rlks'
            if not os.path.exists(base + '.int'):
                out.append(np.zeros(npx, np.complex64)); continue
            z = np.fromfile(base + '.int', dtype=np.complex64).reshape(o['shape5'])
            v = (z != 0) & np.isfinite(z)
            zs = bp.blocks(np.where(v, z, 0), la, lr)
            ph = bp.blocks(np.where(v, z / np.maximum(np.abs(z), 1e-30), 0), la, lr)
            nv = bp.blocks(v.astype(float), la, lr)
            good = nv >= 0.5 * la * lr
            coh = np.where(good, np.abs(ph) / np.maximum(nv, 1), 0)
            out.append(np.where(good, np.exp(1j * np.angle(zs)) * coh, 0).ravel().astype(np.complex64))
    return p, np.concatenate(out)


def main(iargs=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('stack'); a.add_argument('pairs_h5'); a.add_argument('out')
    a.add_argument('--lat', type=float, nargs=2, required=True); a.add_argument('--extra-looks', nargs=2, type=int, default=[2, 4])
    a.add_argument('--nproc', type=int, default=3)
    a = a.parse_args(iargs)
    la, lr = a.extra_looks
    T0 = time.time()
    G = bp.geometry(a.stack, la, lr)
    # pixel order of boi_pairs: swaths in order, overlaps in order, each overlap raveled
    use, idx, base = {}, [], 0
    lat_all = []
    for sw, ovl in G.items():
        use[sw] = []
        for o in ovl:
            lt = o['lat'].ravel(); n = lt.size
            u = np.nanmax(lt) >= a.lat[0] and np.nanmin(lt) <= a.lat[1] if np.isfinite(lt).any() else False
            use[sw].append(u)
            if u:
                idx.append(np.arange(base, base + n))
                lat_all.append(lt)
            base += n
    idx = np.concatenate(idx); lat_used = np.concatenate(lat_all)
    sel = (lat_used >= a.lat[0]) & (lat_used <= a.lat[1])
    pix = idx[sel]
    with h5py.File(a.pairs_h5, 'r') as P:
        assert P['lat'].shape[0] == base, f'pixel count {base} != {P["lat"].shape[0]}'
        assert np.allclose(P['lat'][:][idx], lat_used, equal_nan=True), 'pixel order differs from pairs file'
        pairs = P['pairs'][:].astype(str); dates = P['dates'][:]
        o_chk = P['o'][0:3][:, pix]
        meta = {k: P[k][:][pix] for k in ('lon', 'lat', 'swath', 'ovl', 'freq_hz')}
        dt = 2.055556e-3
    F = 2 * np.pi * dt * meta['freq_hz']
    print(f'geometry {time.time() - T0:.0f} s; overlaps used {sum(map(sum, use.values()))}; pixels in {a.lat}: {pix.size}; '
          f'pairs {len(pairs)}', flush=True)
    _S.update(stack=a.stack, G=G, la=la, lr=lr, use=use)
    npair = len(pairs)
    with h5py.File(a.out, 'w') as g, Pool(a.nproc) as pool:
        d = g.create_dataset('phi', (npair, pix.size), np.complex64, chunks=(min(32, npair), min(32768, pix.size)),
                             compression='gzip', compression_opts=4, shuffle=True)
        for n, (p, v) in enumerate(pool.imap(pair_phi, pairs, chunksize=4)):
            v = v[sel]
            if n < 3:
                ok = np.isfinite(o_chk[n]) & (v != 0)
                e = np.abs(np.angle(v[ok]) / F[ok] - o_chk[n][ok]); err = np.nanmax(e)
                print(f'check {p}: |angle(phi)/F - o| max {err:.2e}, median {np.nanmedian(e):.1e} lines '
                      f'({ok.sum()} pixels; float32 rounding, 1e-5 lines = 0.14 mm)', flush=True)
                assert err < 1e-5, 'complex export does not reproduce o'
            d[n] = v
            if n % 500 == 0:
                print(f'  {n + 1}/{npair} ({time.time() - T0:.0f} s)', flush=True)
        for k, v in meta.items():
            g[k] = v
        g['pix_index'] = pix; g['freq_rad_per_line'] = F.astype(np.float32); g['pairs'] = pairs.astype('S17'); g['dates'] = dates
        g.attrs.update(lat_band=a.lat, looks=f'{5 * la} az x {15 * lr} rg', source=os.path.abspath(a.stack),
                       notes='phi = exp(i angle(sum z)) * coh (phase-only coherence of the block); angle(phi)/F = o of '
                             'the pairs file; o > 0 along heading.')
    print(f'wrote {a.out} {os.path.getsize(a.out) / 1e9:.2f} GB ({time.time() - T0:.0f} s)')


if __name__ == '__main__':
    main()
