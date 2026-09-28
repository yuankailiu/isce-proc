#!/usr/bin/env python3
"""One-page resource summary of a topsStack run, from `topsstack.py report` (logs/report_<date>*.csv).

Rows are the steps (run_01 ... run_28), grouped in coregistration (1-12), interferograms (13-16)
and ionosphere (17-28). Panels, sharing the rows:
  1. timeline: each step from its first start to its last end (wall-clock date)
  2. compute units per step: CPU core-hours + GPU hours x GPU_UNITS, with the cost at RATE
  3. task wall time per step: distribution over the array tasks (box: quartiles, whisker: 5-95 %),
     the number of tasks, and the tasks that did not complete (reruns, timeouts, cancels)
A header line gives the totals and the cost at the three Resnick fiscal-year tiers.

  plot_report.py logs/report_2026-09-28.csv -o pic/report.png --title a076
"""
import argparse
import csv
import os
import re
from collections import defaultdict
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

plt.rcParams.update({'font.size': 11, 'axes.titlesize': 12, 'axes.labelsize': 11,
                     'axes.spines.top': False, 'axes.spines.right': False})
GROUPS = [('coregistration', 1, 12, '#4C72B0'), ('interferograms', 13, 16, '#55A868'),
          ('ionosphere', 17, 28, '#C44E52')]
TIERS = (0.012, 0.007, 0.006)                                   # Resnick $/unit: <=6.4k, 6.4-24k, >24k per FY


def group_of(num):
    for name, lo, hi, color in GROUPS:
        if lo <= num <= hi:
            return name, color
    return 'other', '0.5'


def lighter(color, f=0.55):
    rgb = np.array(matplotlib.colors.to_rgb(color))
    return tuple(rgb + (1 - rgb) * f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('csv', help='logs/report_<date>.csv (the _tasks.csv next to it is read too)')
    ap.add_argument('-o', '--outfile', default='report.png', help='output figure (default: %(default)s)')
    ap.add_argument('--rate', type=float, default=0.012, help='$ per compute unit (default: %(default)s)')
    ap.add_argument('--gpu-units', type=float, default=10, help='compute units per GPU hour (default: %(default)s)')
    ap.add_argument('--title', default='', help='title prefix (e.g. the track)')
    a = ap.parse_args()

    steps = list(csv.DictReader(open(a.csv)))
    steps = [r for r in steps if re.match(r'run_\d+', r['step'])]
    tasks = defaultdict(list)
    tfile = a.csv.replace('.csv', '_tasks.csv')
    if os.path.isfile(tfile):
        for r in csv.DictReader(open(tfile)):
            tasks[r['step']].append(r)
    num = lambda s: int(s[4:6])
    steps.sort(key=lambda r: num(r['step']))
    n = len(steps)
    y = np.arange(n)[::-1]                                       # step 1 on top
    labels = [f'{num(r["step"]):2d} {r["step"][7:]}' for r in steps]
    colors = [group_of(num(r['step']))[1] for r in steps]

    fig, axs = plt.subplots(1, 3, figsize=(17, 0.34 * n + 2.2), sharey=True,
                            gridspec_kw=dict(width_ratios=[1.2, 1, 1.1], wspace=0.08))
    ax1, ax2, ax3 = axs

    # 1. timeline
    ts = lambda x: datetime.fromisoformat(x) if x and x not in ('Unknown', 'None') else None
    for yi, r, c in zip(y, steps, colors):
        s, e = ts(r['start']), ts(r['end'])
        if s and e:
            ax1.barh(yi, mdates.date2num(e) - mdates.date2num(s), left=mdates.date2num(s), height=0.62,
                     color=c, alpha=0.85, lw=0)
            ax1.text(mdates.date2num(e), yi, f' {float(r["wall_span_h"]):.1f} h', va='center', fontsize=8.5, color='0.25')
    ax1.xaxis_date()
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=9.5)
    ax1.set_xlabel('date\nbar: first start to last end of the step')
    ax1.set_title('timeline')
    ax1.grid(axis='x', alpha=0.3)

    # 2. compute units
    cpu = np.array([float(r['alloc_cpu_h']) for r in steps])
    gpu = np.array([float(r.get('gpu_h') or 0) for r in steps]) * a.gpu_units
    ax2.barh(y, cpu, height=0.62, color=colors, lw=0)
    ax2.barh(y, gpu, left=cpu, height=0.62, color=[lighter(c) for c in colors], hatch='///', edgecolor='white', lw=0)
    tot = cpu + gpu
    for yi, u in zip(y, tot):
        if u > 0:
            ax2.text(u, yi, f' \\${u * a.rate:,.2f}' if u * a.rate < 10 else f' \\${u * a.rate:,.0f}',
                     va='center', fontsize=8.5, color='0.25')
    ax2.set_xscale('symlog', linthresh=1)
    ax2.set_xlim(0, tot.max() * 4)
    ax2.set_xlabel(f'compute units = CPU core-h + GPU h x {a.gpu_units:g}\nlabel: cost at \\${a.rate:g} / unit')
    ax2.set_title('compute and cost')
    ax2.grid(axis='x', alpha=0.3)
    share = defaultdict(float)
    for r, u in zip(steps, tot):
        share[group_of(num(r['step']))[0]] += u
    hs = [Patch(color=c, label=f'{g}: {share[g]:,.0f} units ({100 * share[g] / tot.sum():.0f} %)')
          for g, _, _, c in GROUPS if share[g]]
    hs.append(Patch(facecolor='0.85', hatch='///', edgecolor='white', label='GPU part'))
    ax1.legend(handles=hs, loc='lower left', fontsize=9.5, framealpha=0.95, title='compute share', title_fontsize=9.5)

    # 3. task wall time distribution
    for yi, r, c in zip(y, steps, colors):
        t = tasks.get(r['step'], [])
        w = np.array([float(x['wall_s']) for x in t if x['state'].startswith('COMPLETED')]) / 60
        bad = sum(not x['state'].startswith('COMPLETED') for x in t)
        if len(w):
            q5, q25, q50, q75, q95 = np.percentile(w, [5, 25, 50, 75, 95])
            ax3.plot([max(q5, 1e-2), q95], [yi, yi], c=c, lw=1.4, alpha=0.8)
            ax3.barh(yi, q75 - q25, left=q25, height=0.55, color=lighter(c, 0.35), edgecolor=c, lw=1)
            ax3.plot([q50, q50], [yi - 0.27, yi + 0.27], c='0.15', lw=1.6)
        txt = f'{len(t):,}' + (f'  ({bad:,} not completed)' if bad else '')
        ax3.text(1.02, yi, txt, transform=ax3.get_yaxis_transform(), va='center', ha='left', fontsize=8.5,
                 color='#A33A3A' if bad else '0.35')
    ax3.set_xscale('log')
    ax3.set_xlabel('task wall time [min], completed tasks\nbox 25-75 %, bar median, whisker 5-95 %')
    ax3.text(1.02, 1.0, 'tasks', transform=ax3.transAxes, va='bottom', ha='left', fontsize=10, color='0.3')
    ax3.set_title('task wall time')
    ax3.grid(axis='x', alpha=0.3, which='both')
    for ax in axs:
        ax.set_ylim(-0.7, n - 0.3)
        # group separators
        for name, lo, hi, color in GROUPS[1:]:
            k = [i for i, r in enumerate(steps) if num(r['step']) >= lo]
            if k and k[0] > 0:
                ax.axhline(y[k[0]] + 0.5, c='0.6', lw=0.8, ls=':')

    units = tot.sum()
    cpu_h, gpu_h = cpu.sum(), gpu.sum() / a.gpu_units
    s0 = min(ts(r['start']) for r in steps if ts(r['start']))
    e0 = max(ts(r['end']) for r in steps if ts(r['end']))
    fig.suptitle(f'{a.title}  {n} steps, {sum(len(v) for v in tasks.values()):,} tasks, '
                 f'{(e0 - s0).total_seconds() / 86400:.1f} days ({s0:%Y-%m-%d} to {e0:%Y-%m-%d})   |   '
                 f'{cpu_h:,.0f} CPU core-h + {gpu_h:,.1f} GPU h = {units:,.0f} units   |   cost '
                 + ' / '.join(f'\\${units * t:,.0f}' for t in TIERS) + ' at \\$0.012 / 0.007 / 0.006 per unit',
                 fontsize=12, y=0.99)
    fig.subplots_adjust(left=0.145, right=0.87, top=1 - 0.75 / fig.get_figheight(), bottom=0.95 / fig.get_figheight())
    os.makedirs(os.path.dirname(os.path.abspath(a.outfile)), exist_ok=True)
    fig.savefig(a.outfile, dpi=150)
    print(f'{units:,.0f} units, ${units * a.rate:,.2f} -> {a.outfile}')


if __name__ == '__main__':
    main()
