#!/usr/bin/env python
"""BOI: velocity profile across a fault trace from a binned product (boi_bin.py).

x = signed distance east of the trace [km] at the cell latitude (trace interpolated in latitude).
Per latitude band: median v in x bins and the far-field difference v(x in +[d1,d2]) - v(x in -[d2,d1]),
with a bootstrap sigma. v is positive toward the output direction of boi_bin.py (dsc: ~N11E).
Usage: boi_fault_profile.py <bin.txt> <trace lon,lat> [--bands 29.5 31.3 32.8 34.5] [--d1 10 --d2 80]
"""
import sys, argparse
import numpy as np


def main(iargs=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('bins'); p.add_argument('trace')
    p.add_argument('--bands', type=float, nargs='+', default=[29.5, 31.3, 32.8, 34.5])
    p.add_argument('--d1', type=float, default=10); p.add_argument('--d2', type=float, default=80)
    p.add_argument('--out', default=None)
    a = p.parse_args(iargs)
    B = np.loadtxt(a.bins); tr = np.loadtxt(a.trace, delimiter=',' if ',' in open(a.trace).readline() else None)
    tr = tr[np.argsort(tr[:, 1])]
    lon, lat, v, s = B[:, 0], B[:, 1], B[:, 2], B[:, 3]
    lf = np.interp(lat, tr[:, 1], tr[:, 0], left=np.nan, right=np.nan)
    x = (lon - lf) * 111.19 * np.cos(np.radians(lat))
    rng = np.random.default_rng(0)
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    nb = len(a.bands) - 1
    fig, axs = plt.subplots(nb, 1, figsize=(7, 3 * nb), sharex=True)
    edges = np.arange(-150, 151, 10)
    for k in range(nb):
        m = (lat >= a.bands[k]) & (lat < a.bands[k + 1]) & np.isfinite(x)
        xe, ve = x[m], v[m]
        E = ve[(xe >= a.d1) & (xe <= a.d2)]; W = ve[(xe <= -a.d1) & (xe >= -a.d2)]
        if E.size < 5 or W.size < 5:
            print(f'{a.bands[k]:.1f}-{a.bands[k+1]:.1f} N: too few cells (E {E.size}, W {W.size})'); continue
        dv = np.median(E) - np.median(W)
        bs = [np.median(rng.choice(E, E.size)) - np.median(rng.choice(W, W.size)) for _ in range(500)]
        print(f'{a.bands[k]:.1f}-{a.bands[k+1]:.1f} N: east - west (|x| {a.d1:.0f}-{a.d2:.0f} km) = {dv:+.1f} '
              f'+- {np.std(bs):.1f} mm/yr  (cells E {E.size}, W {W.size})')
        ax = axs[k] if nb > 1 else axs
        ax.plot(xe, ve - np.median(W), '.', ms=2, alpha=0.3, color='0.5')
        c = 0.5 * (edges[1:] + edges[:-1])
        md = [np.median(ve[(xe >= edges[q]) & (xe < edges[q + 1])]) - np.median(W)
              if ((xe >= edges[q]) & (xe < edges[q + 1])).sum() >= 5 else np.nan for q in range(c.size)]
        ax.plot(c, md, 'o-', color='C3', ms=4, label='10-km bin median')
        ax.axvline(0, color='k', lw=0.8); ax.axhline(0, color='k', lw=0.5); ax.axhline(5, color='C0', ls='--', lw=0.8, label='+5 mm/yr')
        ax.set_ylim(-25, 25); ax.set_ylabel('v - west median [mm/yr]')
        ax.set_title(f'{a.bands[k]:.1f}-{a.bands[k+1]:.1f} N: east - west = {dv:+.1f} +- {np.std(bs):.1f} mm/yr', fontsize=9)
        ax.legend(fontsize=7, loc='upper left')
    (axs[-1] if nb > 1 else axs).set_xlabel('distance east of the DST trace [km]')
    out = a.out or a.bins.replace('.txt', '_faultprof.png').replace('/boi_bin_', '/pic/boi_bin_')
    fig.savefig(out, dpi=170, bbox_inches='tight'); print('wrote', out)


if __name__ == '__main__':
    main()
