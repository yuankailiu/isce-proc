#!/usr/bin/env python
"""Burst-overlap interferometry (BOI), step 1: per-pair along-track offsets on the topsStack ESD products.

Reads, per pair p = (i, j), swath and overlap, ESD/<pair>/IW<n>/overlap_<bb>.5alks_15rlks.int/.cor (run 07, geometry-only
coregistration) and writes, on a grid of (5*az, 15*rg) single-look pixels (default extra looks 2 x 4 -> 10 x 60):
  o     = angle(sum z) / freq_true                [single-look lines]   ESD offset of the secondary (S_j - S_i convention)
  iota  = -(T_j - T_i), T = ion_azshift_dates      [single-look lines]   ionospheric part of o (check 2: o ~ T_i - T_j)
  coh   = |sum exp(i phi)| / N over the block (phase-only), cor = mean ESD .cor (5x5 phase-only)
  lat, lon, dx_az [m per single-look line, along-track ground spacing], ovl (overlap number), valid (pixel ever valid)

freq_true = 2 pi dt (K_t1 t_1 - K_t2 t_2 + f_2 - f_1) of the stack reference only (reference part of overlap_withDEM.py
overlapSpectralSeparation). The stored freq_<bb>.bin is 3 (pair with the stack reference) or 4 (other pairs) times too
large because the overlap offset files are added to the absolute line index twice (BOI_IMPLEMENTATION.md, check 1b);
the script asserts this ratio on two pairs. topsStack misreg/azimuth (S_d) is not used (computed with the wrong factor);
per-pair / per-date constants are removed in boi_ts.py.

Sign [verify, synthetic test BOI_IMPLEMENTATION.md 5.3]: o > 0 = secondary shifted toward later azimuth lines.

Output: <stack>/boi/boi_pairs_IW<n>.h5, datasets (npair, npix) o, iota (float32), coh, cor (float16).
Usage: boi_pairs.py <stack> [--pairs P ...] [--extra-looks 2 4] [--nproc 4]
"""
import os, sys, time, argparse
import numpy as np
import h5py
from multiprocessing import Pool

TOPS = os.path.expanduser('~/tools/isce2/src/isce2/contrib/stack/topsStack')
sys.path.insert(0, TOPS)
AL, RL = 5, 15            # ESD looks (run 07 -a 5 -r 15)
IAZ, IRG = 28, 96         # ion grid looks on the merged full grid


def get_args(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('stack')
    p.add_argument('--pairs', nargs='*', default=None, help='default: all ESD/<pair>')
    p.add_argument('--extra-looks', nargs=2, type=int, default=[2, 4], metavar=('AZ', 'RG'))
    p.add_argument('--nproc', type=int, default=4)
    p.add_argument('--outdir', default=None, help='default <stack>/boi')
    return p.parse_args(iargs)


def blocks(a, la, lr, func=np.sum):
    ny, nx = (a.shape[0] // la) * la, (a.shape[1] // lr) * lr
    return func(a[:ny, :nx].reshape(ny // la, la, nx // lr, lr), axis=(1, 3))


def ref_freq(b1, b2, y1, y2, n, w):
    """reference-only spectral separation [rad per single-look line] on the (n, w) overlap grid (no offsets: yy = 0)."""
    out = 0.0
    for sgn, b, y0 in ((+1, b1, y1), (-1, b2, y2)):
        azi = np.arange(y0, y0 + n)[:, None] * np.ones((1, w))
        rng = b.startingRange + b.rangePixelSize * np.arange(w)[None, :] * np.ones((n, 1))
        Vs = np.linalg.norm(b.orbit.interpolateOrbit(b.sensingMid, method='hermite').getVelocity())
        Ks = 2 * Vs * b.azimuthSteeringRate / b.radarWavelength
        Ka = b.azimuthFMRate(rng)
        Kt = Ks / (1.0 - Ks / Ka)
        t = (azi - (b.numberOfLines // 2)) * b.azimuthTimeInterval
        out = out + sgn * (Kt * t - b.doppler(rng))
    return out * 2 * np.pi * b1.azimuthTimeInterval


def great_circle(lat1, lon1, lat2, lon2):
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(lon2 - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(a))


def read_rows(path, y0, n, w, dtype=np.float64):
    """rows y0..y0+n of a raw (lines, w) file; dtype from the .xml-less ISCE geometry files (float64 lat/lon)."""
    a = np.memmap(path, dtype=dtype, mode='r')
    return np.array(a.reshape(-1, w)[y0:y0 + n])


def geometry(stack, la, lr):
    """per swath: overlap layout, freq_true on the 5x15 grid and lat/lon/dx_az on the output grid."""
    import isce  # noqa
    import s1a_isce_utils as ut
    full = {sw: ut.loadProduct(f'{stack}/reference/IW{sw}.xml') for sw in (1, 2, 3)
            if os.path.exists(f'{stack}/reference/IW{sw}.xml')}
    t0 = min(p.bursts[0].sensingStart for p in full.values())
    r0 = min(p.bursts[0].startingRange for p in full.values())
    G = {}
    for sw, F in full.items():
        top = ut.loadProduct(f'{stack}/reference/overlap/IW{sw}_top.xml')
        bot = ut.loadProduct(f'{stack}/reference/overlap/IW{sw}_bottom.xml')
        nb0 = F.bursts[0].burstNumber
        ovl = []
        for k, ob in enumerate(top.bursts):
            ii = ob.burstNumber
            obB = bot.bursts[k]
            b1, b2 = F.bursts[ii - nb0], F.bursts[ii + 1 - nb0]
            dt = b1.azimuthTimeInterval
            y1 = int(round((ob.sensingStart - b1.sensingStart).total_seconds() / dt))
            y2 = int(round((obB.sensingStart - b2.sensingStart).total_seconds() / dt))
            n, w = ob.numberOfLines, ob.numberOfSamples
            fr = blocks(ref_freq(b1, b2, y1, y2, n, w), AL, RL, np.mean)           # = looks.py average
            # geometry of the top-burst overlap rows (one extra row for dx_az)
            lat = read_rows(f'{stack}/geom_reference/IW{sw}/lat_{ii:02d}.rdr', y1, n + 1, b1.numberOfSamples)
            lon = read_rows(f'{stack}/geom_reference/IW{sw}/lon_{ii:02d}.rdr', y1, n + 1, b1.numberOfSamples)
            dx = great_circle(lat[:-1], lon[:-1], lat[1:], lon[1:])
            lat, lon = lat[:-1], lon[:-1]
            ok = (lat != 0) & (np.abs(dx) < 100)
            def lk(a, l1, l2):
                s = blocks(np.where(ok, a, 0.0), l1, l2); c = blocks(ok.astype(float), l1, l2)
                return np.where(c > 0, s / np.maximum(c, 1), np.nan)
            lat_o, lon_o, dx_o = (lk(a, AL * la, RL * lr) for a in (lat, lon, dx))
            # merged-grid position of output pixel centres -> ion grid coordinates
            ny, nx = lat_o.shape
            yy = (np.arange(ny) * AL * la + (AL * la - 1) / 2)[:, None] * np.ones((1, nx))
            xx = np.ones((ny, 1)) * (np.arange(nx) * RL * lr + (RL * lr - 1) / 2)[None, :]
            my = (ob.sensingStart - t0).total_seconds() / dt + yy
            mx = (ob.startingRange - r0) / ob.rangePixelSize + xx
            ovl.append(dict(ii=ii, dt=dt, shape5=fr.shape, freq=fr, lat=lat_o, lon=lon_o, dx=dx_o,
                            gy=(my - (IAZ - 1) / 2) / IAZ, gx=(mx - (IRG - 1) / 2) / IRG))
        G[sw] = ovl
    return G


def check_freq(stack, G, pairs, ref_date):
    """assert freq_true = stored freq / 3 (pair reference = stack reference) or / 4 (other pairs)."""
    p3 = next((p for p in pairs if p.startswith(ref_date)), None)
    p4 = next((p for p in pairs if not p.startswith(ref_date)), None)
    for p, k in ((p3, 3.0), (p4, 4.0)):
        if p is None:
            continue
        for sw, ovl in G.items():
            o = ovl[len(ovl) // 2]
            f = f'{stack}/ESD/{p}/IW{sw}/freq_{o["ii"]:02d}.5alks_15rlks.bin'
            if not os.path.exists(f):
                continue
            st = np.fromfile(f, dtype=np.float32).reshape(o['shape5'])
            m = st != 0
            r = np.median(st[m] / o['freq'][m])
            print(f'freq check {p} IW{sw} ovl {o["ii"]:02d}: stored/true = {r:.4f} (expected {k:.0f}); '
                  f'true {np.median(o["freq"][m]) / (2 * np.pi * o["dt"]):.0f} Hz')
            assert abs(r / k - 1) < 0.015, f'freq ratio {r:.4f} != {k}'


_W = {}


def _init(stack, G, la, lr):
    _W.update(stack=stack, G=G, la=la, lr=lr)


def do_pair(p):
    stack, G, la, lr = _W['stack'], _W['G'], _W['la'], _W['lr']
    out = {}
    for sw, ovl in G.items():
        o_l, coh_l, cor_l = [], [], []
        for o in ovl:
            base = f'{stack}/ESD/{p}/IW{sw}/overlap_{o["ii"]:02d}.5alks_15rlks'
            npx = o['lat'].size
            if not os.path.exists(base + '.int'):
                o_l.append(np.full(npx, np.nan, np.float32)); coh_l.append(np.zeros(npx)); cor_l.append(np.zeros(npx))
                continue
            z = np.fromfile(base + '.int', dtype=np.complex64).reshape(o['shape5'])
            c = np.fromfile(base + '.cor', dtype=np.float32).reshape(o['shape5'])
            v = (z != 0) & np.isfinite(z)
            # complex sum over the block; freq varies slowly with range -> block-mean freq
            zs = blocks(np.where(v, z, 0), la, lr)
            ph = blocks(np.where(v, z / np.maximum(np.abs(z), 1e-30), 0), la, lr)
            nv = blocks(v.astype(float), la, lr)
            fr = blocks(np.where(v, o['freq'], 0), la, lr) / np.maximum(nv, 1)
            cm = blocks(np.where(v, c, 0), la, lr) / np.maximum(nv, 1)
            good = nv >= 0.5 * la * lr
            off = np.where(good, np.angle(zs) / np.where(fr != 0, fr, np.nan), np.nan)
            o_l.append(off.ravel().astype(np.float32))
            coh_l.append(np.where(good, np.abs(ph) / np.maximum(nv, 1), 0).ravel())
            cor_l.append(np.where(good, cm, 0).ravel())
        out[sw] = (np.concatenate(o_l), np.concatenate(coh_l).astype(np.float16), np.concatenate(cor_l).astype(np.float16))
    return p, out


def main(iargs=None):
    a = get_args(iargs)
    stack = os.path.abspath(a.stack)
    la, lr = a.extra_looks
    outdir = a.outdir or f'{stack}/boi'
    os.makedirs(outdir, exist_ok=True)
    pairs = a.pairs or sorted(d for d in os.listdir(f'{stack}/ESD') if len(d) == 17)
    dates = sorted({d for p in pairs for d in p.split('_')})
    misd = f'{stack}/misreg/azimuth/dates'
    ref_date = sorted(f[:8] for f in os.listdir(misd)
                      if float(open(f'{misd}/{f}').read().split()[0]) == 0.0)[0]
    t = time.time()
    G = geometry(stack, la, lr)
    print(f'geometry {time.time() - t:.0f} s; stack reference {ref_date}; {len(pairs)} pairs, {len(dates)} dates; '
          f'pixels per swath ' + ', '.join(f'IW{s}: {sum(o["lat"].size for o in v)}' for s, v in G.items()))
    check_freq(stack, G, sorted(os.listdir(f'{stack}/ESD')), ref_date)

    # ionospheric shift per date at every output pixel (bilinear on the ion grid), iota_p = -(T_j - T_i)
    from osgeo import gdal
    from scipy.ndimage import map_coordinates
    gdal.UseExceptions()
    Tpix = {sw: np.zeros((len(dates), sum(o['lat'].size for o in v)), np.float32) for sw, v in G.items()}
    has_ion = os.path.isdir(f'{stack}/ion_azshift_dates')
    if has_ion:
        for k, d in enumerate(dates):
            f = f'{stack}/ion_azshift_dates/{d}.ion'
            if not os.path.exists(f):
                for sw in G: Tpix[sw][k] = np.nan
                continue
            T = gdal.Open(f + '.vrt').ReadAsArray().astype(np.float64)
            for sw, v in G.items():
                Tpix[sw][k] = np.concatenate([map_coordinates(T, [o['gy'].ravel(), o['gx'].ravel()], order=1,
                                                              mode='nearest') for o in v])
    di = {d: k for k, d in enumerate(dates)}

    H = {}
    for sw, v in G.items():
        npx = sum(o['lat'].size for o in v)
        h = h5py.File(f'{outdir}/boi_pairs_IW{sw}.h5', 'w')
        for name, dt_ in (('o', 'f4'), ('iota', 'f4'), ('coh', 'f2'), ('cor', 'f2')):
            h.create_dataset(name, (len(pairs), npx), dtype=dt_, chunks=(1, npx), fillvalue=np.nan if dt_ == 'f4' else 0)
        h['lat'] = np.concatenate([o['lat'].ravel() for o in v]).astype(np.float32)
        h['lon'] = np.concatenate([o['lon'].ravel() for o in v]).astype(np.float32)
        h['dx_az'] = np.concatenate([o['dx'].ravel() for o in v]).astype(np.float32)
        h['ovl'] = np.concatenate([np.full(o['lat'].size, o['ii'], np.int16) for o in v])
        h['freq_hz'] = np.concatenate([blocks(o['freq'], la, lr, np.mean).ravel() / (2 * np.pi * o['dt'])
                                       for o in v]).astype(np.float32)
        h['pairs'] = np.array(pairs, dtype='S17'); h['dates'] = np.array(dates, dtype='S8')
        h['T'] = Tpix[sw]
        h.attrs.update(stack=stack, ref_date=ref_date, swath=sw, esd_looks=(AL, RL), extra_looks=(la, lr),
                       single_look_looks=(AL * la, RL * lr), units_o='single-look lines', has_ion=int(has_ion),
                       iota='-(T_j - T_i)', sign='[verify] o > 0 = secondary shifted toward later lines',
                       freq='reference-only spectral separation (stored freq/3 or /4)')
        H[sw] = h
    t = time.time()
    with Pool(a.nproc, initializer=_init, initargs=(stack, G, la, lr)) as pool:
        for n, (p, out) in enumerate(pool.imap(do_pair, pairs, chunksize=2)):
            i, j = p.split('_')
            for sw, (o, coh, cor) in out.items():
                h = H[sw]
                h['o'][n] = o; h['coh'][n] = coh; h['cor'][n] = cor
                h['iota'][n] = -(Tpix[sw][di[j]] - Tpix[sw][di[i]])
            if n % 50 == 0 or n == len(pairs) - 1:
                print(f'{n + 1}/{len(pairs)} {p}  {time.time() - t:.0f} s', flush=True)
    for h in H.values():
        h.close()
    print('done:', ', '.join(f'{outdir}/boi_pairs_IW{sw}.h5' for sw in H))


if __name__ == '__main__':
    main()
