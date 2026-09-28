#!/usr/bin/env python3
"""Acquisitions after `select`: latitude span per date (top) and the interferogram network (bottom).

Top: one bar per date from the slice footprints in search_results.csv; kept dates (zips in the
data dir) colored by platform, dates moved to not_used/ in grey, the select south/north bound dashed.
Bottom: kept dates on one row per starting-range group (IW1/IW2/IW3 starting ranges from
s1_version.txt; the ionosphere pairing groups by these; the IPF versions of each group are listed). Pairs are arcs (height ~ temporal baseline);
pairs across groups are red. Pairs come from a run file / list (any text with YYYYMMDD_YYYYMMDD),
or, before `stack`, are predicted as each date with its next N dates (stackSentinel.py -c N).

  plot_select.py -d ../data --sn -27 -17 -c 10 -o pic/select_network.png
  plot_select.py -d ../data --sn -27 -17 --pairs run_files/run_16_unwrap
"""
import argparse
import csv
import glob
import os
import re
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

COLORS = {'S1A': '#4C72B0', 'S1B': '#DD8452', 'S1C': '#55A868'}     # seaborn 'deep'
GREY, ARC, CROSS, GAP = '#C8C8C8', '#7A8CA3', '#C47A6E', '#F2E3B3'
plt.rcParams.update({'font.size': 12, 'axes.titlesize': 12, 'axes.labelsize': 12})


def date_of(name):
    return re.search(r'_(\d{8})T\d{6}_', name).group(1)


def load(data):
    """{date: dict(s, n, platform, kept)} from search_results.csv and the zips on disk."""
    kept = {date_of(os.path.basename(f)) for f in glob.glob(os.path.join(data, '*.zip'))}
    moved = {date_of(os.path.basename(f)) for f in glob.glob(os.path.join(data, 'not_used', '*.zip'))}
    acq = {}
    for r in csv.DictReader(open(os.path.join(data, 'search_results.csv'))):
        d = date_of(r['Granule Name'])
        lats = [float(r[k]) for k in ('Near Start Lat', 'Far Start Lat', 'Near End Lat', 'Far End Lat')]
        a = acq.setdefault(d, dict(s=90, n=-90, platform=r['Granule Name'][:3], kept=d in kept, moved=d in moved))
        a['s'], a['n'] = min(a['s'], *lats), max(a['n'], *lats)
    return acq


def groups(data, dates):
    """{date: (IPF version, starting ranges IW1-3 rounded to 1 m)} from s1_version.txt."""
    out = {}
    f = os.path.join(data, 's1_version.txt')
    if not os.path.isfile(f):
        return out
    for line in open(f):
        m = re.match(r'^(S1\w+\.zip)\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)', line)
        if m and date_of(m.group(1)) in dates:
            out.setdefault(date_of(m.group(1)), (m.group(2), tuple(round(float(x)) for x in m.group(3, 4, 5))))
    return out


def read_pairs(path, dates):
    txt = open(path).read()
    ps = sorted({tuple(p.split('_')) for p in re.findall(r'(?<!\d)(\d{8}_\d{8})(?!\d)', txt)})
    return [p for p in ps if p[0] in dates and p[1] in dates]


def predict_pairs(dates, n):
    return [(a, b) for i, a in enumerate(dates) for b in dates[i + 1:i + 1 + n]]


def components(dates, pairs):
    parent = {d: d for d in dates}
    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in pairs:
        parent[root(a)] = root(b)
    return len({root(d) for d in dates})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-d', '--data', required=True, help='data dir with search_results.csv, s1_version.txt, zips')
    ap.add_argument('--sn', type=float, nargs=2, metavar=('S', 'N'), help='select south/north bound (dashed)')
    ap.add_argument('--pairs', help='file with the pairs (e.g. run_files/run_16_unwrap); default: predicted')
    ap.add_argument('-c', '--num-connections', type=int, default=3, help='predicted pairs per date (default: %(default)s)')
    ap.add_argument('-t', '--title', default='', help='figure title prefix (e.g. the track)')
    ap.add_argument('-o', '--outfile', default='select_network.png', help='output figure (default: %(default)s)')
    a = ap.parse_args()

    acq = load(a.data)
    if a.pairs and not any(v['kept'] for v in acq.values()):     # zips deleted: kept = dates of the pairs
        used = {d for p in read_pairs(a.pairs, set(acq)) for d in p}
        for d, v in acq.items():
            v['kept'] = d in used
    dates = sorted(d for d, v in acq.items() if v['kept'])
    if not dates:
        raise SystemExit('no kept dates: no zips in the data dir and no --pairs')
    t = lambda d: datetime.strptime(d, '%Y%m%d')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw=dict(height_ratios=[1, 1.1]))

    # top: latitude span per date
    w = 6                                                            # bar width [days]
    for d, v in sorted(acq.items()):
        c = COLORS.get(v['platform'], 'k') if v['kept'] else GREY
        ax1.bar(t(d), v['n'] - v['s'], bottom=v['s'], width=w, color=c, lw=0)
    hs = [Patch(color=c, label=f'{p} kept ({k})') for p, c in COLORS.items()
          if (k := sum(v['kept'] and v['platform'] == p for v in acq.values()))]
    hs.append(Patch(color=GREY, label=f'not used ({sum(not v["kept"] for v in acq.values())})'))
    if a.sn:
        for y in a.sn:
            ax1.axhline(y, ls='--', c='0.2', lw=1.3)
        ax1.text(0.005, a.sn[1], f' select {a.sn[0]:g} / {a.sn[1]:g}', transform=ax1.get_yaxis_transform(),
                 va='bottom', fontsize=11)
    ax1.set_ylabel('latitude [deg]')
    ax1.set_xlim(t(min(acq)) - timedelta(days=30), t(max(acq)) + timedelta(days=30))
    ax1.legend(handles=hs, loc='lower left', fontsize=11, ncol=4, framealpha=0.9)
    ax1.set_title(f'{a.title} acquisitions: {len(dates)} kept of {len(acq)} dates ({dates[0]} - {dates[-1]})'
                  if dates else f'{a.title} acquisitions: none kept')
    ax1.grid(alpha=0.3)

    # bottom: network on starting-range rows
    g = groups(a.data, set(dates))
    keys = sorted({g[d][1] for d in dates if d in g}, reverse=True)         # starting ranges, one row each
    row = {d: keys.index(g[d][1]) if d in g else -1 for d in dates}
    ipf = {k: sorted({g[d][0] for d in dates if d in g and g[d][1] == k}) for k in keys}
    if a.pairs:
        pairs, how = read_pairs(a.pairs, set(dates)), os.path.basename(a.pairs)
    else:
        pairs, how = predict_pairs(dates, a.num_connections), f'predicted, {a.num_connections} per date'
    tmax = max([(t(b) - t(a_)).days for a_, b in pairs] or [1])
    cross = 0
    for p, q in pairs:
        x0, x1 = mdates.date2num(t(p)), mdates.date2num(t(q))
        y0, y1 = row[p], row[q]
        if y0 == y1:
            xs = np.linspace(x0, x1, 20)
            h = 0.45 * np.sqrt((x1 - x0) / tmax)
            ax2.plot(xs, y0 + h * np.sin(np.pi * (xs - x0) / (x1 - x0)), c=ARC, lw=0.8, alpha=0.55)
        else:
            cross += 1
            ax2.plot([x0, x1], [y0, y1], c=CROSS, lw=0.9, alpha=0.5)
    for d in dates:
        ax2.plot(t(d), row[d], 'o', ms=6, mec='white', mew=0.6, c=COLORS.get(acq[d]['platform'], 'k'), zorder=3)
    gaps = [(dates[i], dates[i + 1], (t(dates[i + 1]) - t(dates[i])).days) for i in range(len(dates) - 1)]
    for p, q, n in gaps:
        if n > 36:
            ax2.axvspan(t(p), t(q), color=GAP, alpha=0.6, lw=0)
    ax2.set_yticks(range(len(keys)))
    ax2.set_yticklabels([f'IW1 {k[0] / 1e3:.2f} km\nIPF {ipf[k][0]}-{ipf[k][-1]}\n'
                         f'{sum(row[d] == i for d in dates)} dates' for i, k in enumerate(keys)], fontsize=10)
    ax2.set_ylim(-0.6, len(keys) - 0.4)
    ncomp = components(dates, pairs)
    ax2.set_title(f'network ({how}): {len(pairs)} pairs, {cross} across starting-range groups (muted red), '
                  f'\n'
                  f'{ncomp} connected component{"s" if ncomp != 1 else ""}; gaps > 36 d shaded '
                  f'({sum(n > 36 for *_, n in gaps)}, max {max([n for *_, n in gaps] or [0])} d)')
    ax2.grid(alpha=0.3, axis='x')
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(a.outfile)), exist_ok=True)
    fig.savefig(a.outfile, dpi=150)
    print(f'{len(dates)} kept dates, {len(pairs)} pairs ({how}), {cross} across groups, {ncomp} component(s) '
          f'-> {a.outfile}')


if __name__ == '__main__':
    main()
