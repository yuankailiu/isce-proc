#!/usr/bin/env python3
"""One-page resource summary of a topsStack run, from `topsstack.py report` (logs/report_<date>*.csv).

Rows are the steps (run_01 ... run_28), grouped in coregistration (1-12), interferograms (13-16)
and ionosphere (17-28). Panels, sharing the rows:
Terms (Slurm): each step is submitted as one or more job arrays (one per .job file, and again for
reruns); each array task (JobID <array>_<index>, --nodes=1 --ntasks=1) runs one row, i.e. one line
of the run file. Array tasks of all submissions are counted.
  1. timeline: one bar per job array of the step, first start to last end; amber if most of its
     array tasks failed or were cancelled. Campaigns (processing periods separated by more than
     --campaign-gap days without any array task, i.e. a later decision to rerun or add steps) are
     drawn side by side; the idle time between them is not counted as wall time
  (extra in 2. is estimated per campaign, so a later campaign's deliberate rerun counts as needed)
  2. compute units per step: CPU core-hours + GPU hours x GPU_UNITS, with the cost at RATE; red part:
     extra = total - rows x median completed array task (reruns, failed, cancelled; an estimate)
  3. wall time per array task: distribution over the completed array tasks (box: quartiles,
     whisker: 5-95 %); right: rows of the run file and the extra array tasks (array tasks - rows)
A header line gives the totals and the cost at RATE (hpc.costPerCpuHour).

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

plt.rcParams.update({'font.size': 13, 'axes.titlesize': 14, 'axes.labelsize': 13,
                     'axes.spines.top': False, 'axes.spines.right': False})
GROUPS = [('coregistration', 1, 12, '#4C72B0'), ('interferograms', 13, 16, '#55A868'),
          ('ionosphere', 17, 28, '#C44E52')]


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
    ap.add_argument('--campaign-gap', type=float, default=14, metavar='DAYS',
                    help='idle time that separates processing campaigns (a later decision to rerun or add steps): '
                         'cut from the timeline and from the wall time; compute is counted (default: %(default)s)')
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
    ts = lambda x: datetime.fromisoformat(x) if x and x not in ('Unknown', 'None') else None
    # started job arrays per step; rows with several job arrays get more height (one sub-bar each)
    narr = [len({x['jobid'].split('_')[0] for x in tasks.get(r['step'], []) if ts(x['start'])}) for r in steps]
    hrow = np.array([min(1 + 0.3 * (max(k, 1) - 1), 3.2) for k in narr])
    edge = np.concatenate([[0], np.cumsum(hrow)])               # top of row i at total - edge[i]
    total = edge[-1]
    y = total - (edge[:-1] + hrow / 2)                           # step 1 on top
    labels = [f'{num(r["step"]):2d} {r["step"][7:]}' for r in steps]
    colors = [group_of(num(r['step']))[1] for r in steps]

    fig, axs = plt.subplots(1, 3, figsize=(18, 0.42 * total + 2.0), sharey=True,
                            gridspec_kw=dict(width_ratios=[1.15, 1, 1.05], wspace=0.05))
    ax1, ax2, ax3 = axs

    done = lambda x: x['state'].startswith('COMPLETED')
    unit = lambda x: float(x['wall_s']) / 3600 * (float(x['ncpu']) + float(x['ngpu']) * a.gpu_units)
    RERUN = '#E0A458'                                            # muted amber: failed / cancelled / repeated work

    # campaigns: periods of processing separated by more than --campaign-gap days of no array task running
    # (decision time, e.g. adding the ionosphere months later), not computation time
    GAP_DAYS = a.campaign_gap
    iv = sorted((ts(x['start']), ts(x['end'])) for t in tasks.values() for x in t if ts(x['start']) and ts(x['end']))
    segs = []
    for s_, e_ in iv:
        if segs and (s_ - segs[-1][1]).total_seconds() <= GAP_DAYS * 86400:
            segs[-1][1] = max(segs[-1][1], e_)
        else:
            segs.append([s_, e_])
    seglen = [mdates.date2num(e_) - mdates.date2num(s_) for s_, e_ in segs] or [1]
    spacer = 0.06 * sum(seglen) if len(segs) > 1 else 0
    offs = np.concatenate([[0], np.cumsum([l + spacer for l in seglen])])
    camp = lambda t: next((k for k, (s_, e_) in enumerate(segs) if t <= e_), len(segs) - 1)
    def X(t):                                                    # date -> compressed axis coordinate
        d = mdates.date2num(t)
        for k, (s_, e_) in enumerate(segs):
            if d <= mdates.date2num(e_) or k == len(segs) - 1:
                return offs[k] + max(d - mdates.date2num(s_), 0)
        return d
    for yi, hr, r, c in zip(y, hrow, steps, colors):
        arrays = defaultdict(list)
        for x in tasks.get(r['step'], []):
            if ts(x['start']) and ts(x['end']):
                arrays[x['jobid'].split('_')[0]].append(x)
        arrays = sorted(arrays.values(), key=lambda t: min(ts(x['start']) for x in t))
        k = max(len(arrays), 1)
        h = 0.0                                                  # active hours: sum of the step's spans per campaign
        for kc in sorted({camp(ts(x['start'])) for t in arrays for x in t}):
            xs = [x for t in arrays for x in t if camp(ts(x['start'])) == kc]
            s1, e1 = min(ts(x['start']) for x in xs), max(ts(x['end']) for x in xs)
            ax1.barh(yi, X(e1) - X(s1), left=X(s1), height=hr - 0.12, color=c, alpha=0.12, lw=0)
            h += (e1 - s1).total_seconds() / 3600
        hgt = (hr - 0.2) / k
        for m, t in enumerate(arrays):
            s0_, e0_ = min(ts(x['start']) for x in t), max(ts(x['end']) for x in t)
            frac = sum(map(done, t)) / len(t)
            ax1.barh(yi + (hr - 0.2) / 2 - hgt * (m + 0.5), max(X(e0_) - X(s0_), 0.004 * offs[-1]),
                     left=X(s0_), height=hgt * 0.9, color=c if frac >= 0.5 else RERUN, alpha=0.9, lw=0)
        e = max((ts(x['end']) for t in arrays for x in t), default=None)
        if e:
            ax1.text(X(e) + 0.006 * offs[-1], yi, (f'{h:.1f} h' if h >= 1 else f'{h * 60:.0f} min')
                     + (f', {len(arrays)} arrays' if len(arrays) > 1 else ''), va='center', fontsize=10.5, color='0.25')
    ax1.set_xlim(-0.02 * offs[-1], offs[-1] * 1.26)               # room for the labels of the last steps
    ticks, tlabels = [], []
    for k, (s_, e_) in enumerate(segs):
        step_d = max(1, int(np.ceil(seglen[k] / max(2, 6 * seglen[k] / sum(seglen)))))
        d0 = mdates.date2num(datetime(s_.year, s_.month, s_.day)) + 1
        ticks.append(offs[k])
        tlabels.append(s_.strftime('%m-%d' if len(segs) == 1 else '%y-%m-%d'))
        for d in np.arange(d0 + (step_d - 1 if len(segs) > 1 else 0), mdates.date2num(e_) + 1e-9, step_d):
            if ticks and offs[k] + d - mdates.date2num(s_) - ticks[-1] < 0.08 * offs[-1]:
                continue
            ticks.append(offs[k] + d - mdates.date2num(s_))
            tlabels.append(mdates.num2date(d).strftime('%m-%d' if len(segs) == 1 else '%y-%m-%d'))
        if k < len(segs) - 1:                                    # break mark: idle gap cut out
            xb = offs[k] + seglen[k] + spacer / 2
            ax1.axvline(xb, c='0.45', lw=1.2, ls=(0, (4, 3)))
            ax1.text(xb, 0.5, f' {(segs[k + 1][0] - e_).days} d gap ', transform=ax1.get_xaxis_transform(), rotation=90,
                     ha='center', va='center', fontsize=10.5, color='0.35', bbox=dict(fc='white', ec='none', pad=1))
    ax1.set_xticks(ticks)
    ax1.set_xticklabels(tlabels, rotation=30 if len(segs) > 1 else 0, ha='right' if len(segs) > 1 else 'center')
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=12)
    ax1.set_xlabel('date\n(bar: job array; band: step span within a campaign)')
    ax1.set_title('timeline')
    ax1.grid(axis='x', alpha=0.3)

    # 2. compute units: needed (rows x median completed array task) and extra (the rest: reruns, failed, cancelled)
    cpu = np.array([float(r['alloc_cpu_h']) for r in steps])
    gpu = np.array([float(r.get('gpu_h') or 0) for r in steps]) * a.gpu_units
    tot = cpu + gpu
    extra = np.zeros(n)
    for i, r in enumerate(steps):                                # per campaign: a later campaign's rerun is needed work
        rows = int(r['rows']) if r.get('rows') else None
        byc = defaultdict(list)
        for x in tasks.get(r['step'], []):
            byc[camp(ts(x['start'])) if ts(x['start']) else -1].append(x)
        for kc, t in byc.items():
            ok = [unit(x) for x in t if done(x)]
            if kc >= 0 and rows and ok:
                extra[i] += max(sum(map(unit, t)) - min(rows, len(ok)) * float(np.median(ok)), 0)
            else:
                extra[i] += sum(unit(x) for x in t if not done(x))
        extra[i] = min(extra[i], tot[i])
    need = tot - extra
    gshare = np.where(tot > 0, gpu / np.maximum(tot, 1e-9), 0)
    ax2.barh(y, need * (1 - gshare), height=0.78, color=colors, lw=0)
    ax2.barh(y, need * gshare, left=need * (1 - gshare), height=0.78, color=[lighter(c) for c in colors],
             hatch='///', edgecolor='white', lw=0)
    ax2.barh(y, extra, left=need, height=0.78, color=RERUN, alpha=0.75, lw=0)
    for yi, u, x_ in zip(y, tot, extra):
        if u > 0:
            cost = u * a.rate
            lab = '<\\$0.01' if cost < 0.01 else (f'\\${cost:,.2f}' if cost < 10 else f'\\${cost:,.0f}')
            if x_ >= 0.05 * u and x_ * a.rate >= 0.01:
                lab += f' ({100 * x_ / u:.0f} %)'
            ax2.text(u * 1.08, yi, lab, va='center', fontsize=11, color='0.25')
    ax2.set_xscale('symlog', linthresh=1)
    ax2.set_xlim(0, tot.max() * 30)
    ax2.set_xlabel(f'compute units\n(CPU core-h + {a.gpu_units:g} x GPU h; label: cost)')
    ax2.set_title('compute and cost')
    ax2.grid(axis='x', alpha=0.3)
    share = defaultdict(float)
    for r, u in zip(steps, need):
        share[group_of(num(r['step']))[0]] += u
    hs = [Patch(color=c, label=f'{g}: {share[g]:,.0f} units') for g, _, _, c in GROUPS if share[g]]
    hs.append(Patch(facecolor='0.85', hatch='///', edgecolor='white', label='GPU part'))
    hs.append(Patch(color=RERUN, alpha=0.75, label=f'extra: {extra.sum():,.0f} units ({100 * extra.sum() / tot.sum():.0f} %)\n'
                                                   '(failed, cancelled, repeats\nwithin a campaign)'))
    ax1.legend(handles=hs, loc='upper right', fontsize=11.5, framealpha=0.95, title='compute units', title_fontsize=11.5)

    # 3. wall time per array task
    for yi, r, c in zip(y, steps, colors):
        t = tasks.get(r['step'], [])
        w = np.array([float(x['wall_s']) for x in t if done(x)]) / 60
        bad = sum(not done(x) for x in t)
        if len(w):
            q5, q25, q50, q75, q95 = np.percentile(w, [5, 25, 50, 75, 95])
            ax3.plot([max(q5, 1e-2), q95], [yi, yi], c=c, lw=2, alpha=0.8)
            ax3.barh(yi, max(q75 - q25, q50 * 0.08), left=q25, height=0.7, color=lighter(c, 0.35), edgecolor=c, lw=1.2)
            ax3.plot([q50, q50], [yi - 0.35, yi + 0.35], c='0.15', lw=2)
        rows = int(r['rows']) if r.get('rows') else None
        if rows is not None:                                     # extra array tasks per campaign, as in panel 2
            nc = defaultdict(int)
            for x in t:
                nc[camp(ts(x['start'])) if ts(x['start']) else -1] += 1
            more = sum(max(v - rows, 0) if k >= 0 else v for k, v in nc.items())
        else:
            more = bad
        ax3.text(1.02, yi, f'{rows:,}' if rows is not None else f'{len(t):,}', transform=ax3.get_yaxis_transform(),
                 va='center', ha='left', fontsize=11, color='0.3')
        if more > 0:
            ax3.text(1.155, yi, f'+{more:,}', transform=ax3.get_yaxis_transform(), va='center', ha='left',
                     fontsize=11, color=RERUN)
    ax3.set_xscale('log')
    ax3.set_xlabel('wall time per array task [min], completed ones\n(box 25-75 %, bar median, whisker 5-95 %)')
    ax3.text(1.02, 1.0, 'rows', transform=ax3.transAxes, va='bottom', ha='left', fontsize=11.5, color='0.3')
    ax3.text(1.155, 1.0, 'extra', transform=ax3.transAxes, va='bottom', ha='left', fontsize=11.5, color=RERUN)
    ax3.set_title('array task wall time')
    ax3.grid(axis='x', alpha=0.3, which='both')
    for ax in axs:
        ax.set_ylim(0, total)
        # group separators
        for name, lo, hi, color in GROUPS[1:]:
            k = [i for i, r in enumerate(steps) if num(r['step']) >= lo]
            if k and k[0] > 0:
                ax.axhline(total - edge[k[0]], c='0.6', lw=0.8, ls=':')

    units = tot.sum()
    cpu_h, gpu_h = cpu.sum(), gpu.sum() / a.gpu_units
    nrows = sum(int(r['rows']) for r in steps if r.get('rows'))
    ntask = sum(len(v) for v in tasks.values())
    s0 = min(ts(r['start']) for r in steps if ts(r['start']))
    e0 = max(ts(r['end']) for r in steps if ts(r['end']))
    fig.suptitle(f'{a.title}:  \\${units * a.rate:,.0f}  ({units:,.0f} compute units),  '
                 + (f'{sum(seglen):.1f} days' if len(segs) == 1 else f'{sum(seglen):.1f} days in {len(segs)} campaigns'),
                 fontsize=16, y=0.997)
    detail = (f'{n} steps, {nrows:,} rows, {ntask:,} array tasks;  {cpu_h:,.0f} CPU core-h + {gpu_h:,.0f} GPU h '
              f'(x {a.gpu_units:g}) = {units:,.0f} units at \\${a.rate:g} / unit;  extra {extra.sum():,.0f} units '
              f'({100 * extra.sum() / max(tot.sum(), 1e-9):.0f} %)\n'
              + ('campaign: ' if len(segs) == 1 else 'campaigns: ')
              + ',  '.join(f'{s_:%Y-%m-%d %H:%M} to {e_:%Y-%m-%d %H:%M} ({l:.1f} d)' for (s_, e_), l in zip(segs, seglen))
              + (f';  gaps > {GAP_DAYS:g} d without array tasks separate campaigns and are not counted as time'
                 if len(segs) > 1 else ''))
    fig.text(0.5, 0.004, detail, ha='center', va='bottom', fontsize=11.5, color='0.3')
    fig.subplots_adjust(left=0.178, right=0.875, top=1 - 0.85 / fig.get_figheight(), bottom=(1.55 if len(segs) == 1 else 1.95) / fig.get_figheight())
    os.makedirs(os.path.dirname(os.path.abspath(a.outfile)), exist_ok=True)
    fig.savefig(a.outfile, dpi=150)
    print(f'{units:,.0f} units, ${units * a.rate:,.2f} -> {a.outfile}')


if __name__ == '__main__':
    main()
