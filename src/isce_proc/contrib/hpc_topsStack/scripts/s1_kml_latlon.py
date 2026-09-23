#!/usr/bin/env python3
"""Plot the latitude span of Sentinel-1 acquisitions over time.

Reads one unified ASF search-results KML (or several files), splits the
placemarks into series automatically by platform / path / direction, and solves
for a latitude window to keep for the InSAR stack.
"""
import re
import os
import itertools
import argparse
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


# fixed colors so a given platform always looks the same across figures
PLATFORM_COLORS = {
    'S1A': 'tab:blue',
    'S1B': 'tab:orange',
    'S1C': 'tab:green',
    'S1D': 'tab:red',
}
EPILOG = """examples:
  %(prog)s search_results.kml                 auto groups + solved window
  %(prog)s search_results.kml -p              add the Pareto trade-off table
  %(prog)s search_results.kml --min-keep 0.9  keep >=90%% of acquisitions
  %(prog)s search_results.kml -w 2            buy extent, spend epochs
  %(prog)s search_results.kml --extent -25 -16   force the window
  %(prog)s a076.kml d054.kml -n asc desc      several files, labelled
"""

SHADE_COLOR = '#e6e6e6'   # solid, so it survives transparent=True
FALLBACK_COLORS = ['tab:blue', 'tab:orange', 'tab:green', 'tab:red', 'tab:purple',
                   'tab:brown', 'tab:pink', 'tab:olive', 'tab:cyan', 'tab:gray']

GROUP_FIELDS = ['source', 'platform', 'path', 'direction']


def read_kml_latitudes(kml_file):
    """Reads latitude span + platform / path / direction from a KML file."""
    with open(kml_file, 'r') as f:
        content = f.read()

    placemarks = re.findall(r"<Placemark>(.*?)</Placemark>", content, re.DOTALL)
    rows = []

    for block in placemarks:
        name_match = re.search(r"<name>(.*?)</name>", block)
        name = name_match.group(1) if name_match else ""

        # acquisition date: prefer the yyyymmddThhmmss stamp, else any 8 digits
        date_match = re.search(r'(\d{8})T\d{6}', name) or re.search(r'(\d{8})', name)
        start_date_str = date_match.group(1) if date_match else None

        # platform from the granule name (S1A_IW_SLC__..., S1C_IW_SLC__...)
        plat_match = re.match(r'\s*(S1[A-Z])', name)
        platform = plat_match.group(1) if plat_match else None

        # path / direction live in the CDATA description block
        path_match = re.search(r'Path:\s*(\d+)', block)
        path = f"P{int(path_match.group(1)):03d}" if path_match else None

        dir_match = re.search(r'Ascending/Descending:\s*([A-Za-z]+)', block)
        direction = dir_match.group(1).upper()[:3] if dir_match else None   # ASC / DES

        coords_match = re.search(r"<coordinates>(.*?)</coordinates>", block, re.DOTALL)
        coords_str = coords_match.group(1).strip() if coords_match else ""
        lats = [float(c.split(',')[1]) for c in coords_str.split()] if coords_str else []

        # Add row only if essential data is found
        if start_date_str and lats:
            rows.append([
                start_date_str,
                datetime.strptime(start_date_str, '%Y%m%d'),
                min(lats),
                max(lats),
                platform,
                path,
                direction,
            ])

    return pd.DataFrame(rows, columns=['date', 'datetime', 'south', 'north',
                                       'platform', 'path', 'direction'])


def read_log_latitudes(log_file):
    """Reads latitude span data from a custom log file format."""
    rows = []
    with open(log_file, 'r') as f:
        found_header = False
        for line in f:
            if line.startswith('date      south       north'):
                found_header = True
                continue
            if found_header:
                if line.startswith('****************'):
                    break
                parts = line.strip().split()
                if len(parts) == 3: # Simple check for correct number of parts
                    d, s, n = parts
                    rows.append([d, datetime.strptime(d, '%Y%m%d'), float(s), float(n),
                                 None, None, None])
    return pd.DataFrame(rows, columns=['date', 'datetime', 'south', 'north',
                                       'platform', 'path', 'direction'])


def read_file(file_path):
    """Dispatch on file extension; returns a dataframe (possibly empty)."""
    if file_path.endswith('.kml'):
        return read_kml_latitudes(file_path)
    if file_path.endswith('.log') or file_path.endswith('.txt'):
        return read_log_latitudes(file_path)
    print(f"Error: Unsupported file type for '{file_path}'. Skipping.")
    return pd.DataFrame(columns=['date', 'datetime', 'south', 'north',
                                 'platform', 'path', 'direction'])


def pick_group_fields(df, requested=None):
    """Decide which columns define a legend group.

    auto: always split on platform (when known) and additionally on any of
    source / path / direction that actually varies in the input.
    """
    if requested:
        if 'none' in requested:
            return []
        return [f for f in requested if f in df.columns]

    fields = []
    for field in GROUP_FIELDS:
        col = df[field]
        if col.isna().all():
            continue
        nuniq = col.nunique(dropna=True)
        if field == 'platform':
            if nuniq >= 1:          # keep platform in the label even if unique
                fields.append(field)
        elif nuniq > 1:
            fields.append(field)
    return fields


def split_groups(df, group_fields):
    """Return ordered dict-like list of (label, dataframe)."""
    if not group_fields:
        return [('All acquisitions', df)]

    groups = []
    for key, sub in df.groupby(group_fields, dropna=False, sort=True):
        if not isinstance(key, tuple):
            key = (key,)
        label = ' '.join(str(k) for k in key if k is not None and str(k) != 'nan')
        groups.append((label or 'unknown', sub))
    return groups


def assign_colors(groups, group_fields):
    """Fixed per-platform colors when platform is the only split, else cycle."""
    labels = [lab for lab, _ in groups]
    if group_fields == ['platform'] and all(lab in PLATFORM_COLORS for lab in labels):
        return {lab: PLATFORM_COLORS[lab] for lab in labels}
    cycle = itertools.cycle(FALLBACK_COLORS)
    return {lab: next(cycle) for lab in labels}


def merge_frames(df):
    """Collapse the frames of one pass into a single lat span.

    A pass is identified by date + path + direction, so that unrelated tracks
    acquired on the same day are never fused (matters with '-g none').
    """
    keys = ['date'] + [f for f in ('path', 'direction')
                       if f in df.columns and df[f].notna().any()]
    out = (df.groupby(keys, as_index=False, dropna=False)
             .agg(datetime=('datetime', 'first'),
                  south=('south', 'min'),
                  north=('north', 'max')))
    return out.sort_values('datetime')


def stack_fields(df):
    """Columns that separate independent InSAR stacks (a track = path+direction)."""
    return [f for f in ('path', 'direction')
            if f in df.columns and df[f].nunique(dropna=True) > 1]


def best_extent(passes, weight=1.0, min_keep=0.0):
    """Pick a latitude window trading spatial coverage against temporal sampling.

    A pass is 'kept' only if its own span fully contains the window, so widening
    the window drops passes.  We build the Pareto front of (extent, n_kept) over
    every candidate window and take the point maximising

        score = (north - south) ** weight * n_kept

    i.e. the largest-area rectangle under that front -- parameter free at
    weight=1.  weight > 1 buys extent at the cost of epochs, weight < 1 does the
    opposite.  min_keep sets a hard floor on the fraction of passes retained.
    """
    south = passes['south'].to_numpy(dtype=float)
    north = passes['north'].to_numpy(dtype=float)
    total = south.size
    if total == 0:
        return None

    cand_s = np.unique(south)
    cand_n = np.unique(north)

    # enumerate every candidate window as flat arrays
    all_s, all_n, all_ext, all_kept = [], [], [], []
    for s_cand in cand_s:
        sub = np.sort(north[south <= s_cand])
        if sub.size == 0:
            continue
        valid = cand_n[cand_n > s_cand]
        if valid.size == 0:
            continue
        kept = sub.size - np.searchsorted(sub, valid, side='left')
        good = kept > 0
        if not good.any():
            continue
        all_s.append(np.full(int(good.sum()), s_cand))
        all_n.append(valid[good])
        all_ext.append(valid[good] - s_cand)
        all_kept.append(kept[good])

    if not all_ext:
        return None

    s_arr = np.concatenate(all_s)
    n_arr = np.concatenate(all_n)
    ext_arr = np.concatenate(all_ext)
    kept_arr = np.concatenate(all_kept)

    # widest window for each achievable number of kept passes
    order = np.lexsort((ext_arr, kept_arr))          # by kept, then extent
    s_arr, n_arr = s_arr[order], n_arr[order]
    ext_arr, kept_arr = ext_arr[order], kept_arr[order]
    _, first = np.unique(kept_arr, return_index=True)
    last = np.append(first[1:], kept_arr.size) - 1   # widest window per kept level

    # Pareto front: scan kept descending, keep the strictly wider windows
    front = []
    widest = -np.inf
    for i in last[::-1]:
        if ext_arr[i] > widest:
            widest = ext_arr[i]
            front.append(i)

    curve = [(float(ext_arr[i]), int(kept_arr[i]),
              float(s_arr[i]), float(n_arr[i])) for i in front]

    floor = int(np.ceil(min_keep * total))
    feasible = [c for c in curve if c[1] >= floor]
    if not feasible:
        return None
    ext, kept, s_best, n_best = max(feasible, key=lambda c: c[0] ** weight * c[1])

    return dict(south=s_best, north=n_best, extent=ext, kept=kept,
                total=total, score=ext ** weight * kept, curve=curve)


def extent_summary(label, ext):
    """One terse line."""
    return (f"extent [{label}]: {ext['south']:+.2f} to {ext['north']:+.2f} deg "
            f"({ext['extent']:.2f} deg span) | keeps {ext['kept']}/{ext['total']} "
            f"acq ({100.0 * ext['kept'] / ext['total']:.0f}%)")


def extent_tradeoff_table(ext, nrow=14):
    """Pareto front of the coverage / sampling trade-off, around the pick."""
    curve = ext['curve']
    if not curve:
        return
    step = max(1, len(curve) // nrow)
    rows = curve[::step]
    chosen = (ext['extent'], ext['kept'], ext['south'], ext['north'])
    if chosen not in rows:
        rows = sorted(rows + [chosen], key=lambda c: -c[1])
    print("    south    north   extent   acq kept   frac")
    for extent, kept, s_lat, n_lat in rows:
        mark = '  <-- chosen' if (extent, kept, s_lat, n_lat) == chosen else ''
        print(f"   {s_lat:+7.2f}  {n_lat:+7.2f}  {extent:7.2f}   {kept:8d}   "
              f"{100.0 * kept / ext['total']:4.0f}%{mark}")


def plot_groups(groups, colors, extents=None, outfile='epochs_latlon.png',
                merge=True, show=True, shade=True):
    """Plots the latitude spans over time for each group."""
    plt.rcParams.update({
        'font.size': 12,
        'axes.titlesize': 14,
        'axes.labelsize': 12,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10,
    })

    fig, ax = plt.subplots(figsize=[10, 6])
    legend_elements = {}

    for label, df in groups:
        df = df.dropna(subset=['datetime', 'south', 'north'])
        if df.empty:
            continue
        df = merge_frames(df) if merge else df.sort_values('datetime')

        line_collection = ax.vlines(x=df['datetime'],
                                    ymin=df['south'],
                                    ymax=df['north'],
                                    color=colors[label], linewidth=2, alpha=0.85)
        legend_elements[f"{label} ({len(df)})"] = line_collection

    # chosen latitude window(s) to keep for the InSAR stack
    for label, ext, t0, t1 in (extents or []):
        if t0 is None:
            if shade:
                ax.axhspan(ext['south'], ext['north'], facecolor=SHADE_COLOR,
                           edgecolor='none', zorder=0)
            for y in (ext['south'], ext['north']):
                ax.axhline(y, color='k', ls='--', lw=1.4, zorder=5)
            xtext = ax.get_xlim()[0]
        else:
            if shade:
                ax.fill_between([t0, t1], ext['south'], ext['north'],
                                facecolor=SHADE_COLOR, edgecolor='none', zorder=0)
            ax.hlines([ext['south'], ext['north']], t0, t1,
                      color='k', ls='--', lw=1.4, zorder=5)
            xtext = t0
        for y, va in ((ext['north'], 'bottom'), (ext['south'], 'top')):
            ax.text(xtext, y, f" {y:+.2f}", color='k', fontsize=9,
                    ha='left', va=va, zorder=6)
        key = (f"keep {ext['south']:+.2f}/{ext['north']:+.2f}"
               f"  {ext['kept']}/{ext['total']} acq"
               + (f"  [{label}]" if label != 'all' else ''))
        legend_elements[key] = Line2D([0], [0], color='k', ls='--', lw=1.4)

    ax.legend(handles=legend_elements.values(), labels=legend_elements.keys(), loc='best')

    ax.set_xlabel("Acquisition Time")
    ax.set_ylabel("Latitude (South to North)")
    ax.set_title("Latitude Span of Sentinel-1 Acquisitions Over Time")
    ax.grid(True, linestyle='--', alpha=0.6)

    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax.xaxis.set_minor_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    fig.autofmt_xdate()

    plt.tight_layout()
    plt.savefig(outfile, transparent=True, dpi=300, bbox_inches='tight')
    print(f"saved figure: {os.path.abspath(outfile)}")
    if show:
        plt.show()


def solve_extents(data, args):
    """Latitude keep-window per independent stack (track); [] if disabled."""
    if not args.do_extent:
        return []

    clean = data.dropna(subset=['datetime', 'south', 'north'])
    if clean.empty:
        return []

    fields = stack_fields(clean)
    if fields:
        stacks = []
        for key, sub in clean.groupby(fields, dropna=False, sort=True):
            key = key if isinstance(key, tuple) else (key,)
            stacks.append((' '.join(str(k) for k in key), sub))
    else:
        stacks = [('all', clean)]

    out = []
    for label, sub in stacks:
        passes = merge_frames(sub)
        if args.extent is not None:
            s_lat, n_lat = sorted(args.extent)
            kept = int(((passes['south'] <= s_lat) & (passes['north'] >= n_lat)).sum())
            ext = dict(south=s_lat, north=n_lat, extent=n_lat - s_lat,
                       kept=kept, total=len(passes), score=float('nan'))
        else:
            ext = best_extent(passes, weight=args.weight, min_keep=args.min_keep)
        if ext is None:
            continue
        t0 = t1 = None
        if len(stacks) > 1:
            t0, t1 = passes['datetime'].min(), passes['datetime'].max()
        out.append((label, ext, t0, t1))
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument('file_paths', type=str, nargs='+',
        help='input KML / log file(s); one unified KML is enough')
    parser.add_argument('-n', '--names', dest='names', type=str, nargs='*',
        metavar='NAME',
        help='label per input file, used as an extra grouping level')
    parser.add_argument('-g', '--group-by', dest='group_by', type=str, nargs='+',
        choices=GROUP_FIELDS + ['none'], default=None, metavar='FIELD',
        help='override auto grouping: source|platform|path|direction|none')
    parser.add_argument('--no-merge', dest='merge', action='store_false',
        help='draw each frame, not one merged span per pass')

    keep = parser.add_argument_group(
        'keep-window',
        'A pass is kept only if its span fully contains the window, so a wider\n'
        'window costs epochs. The solver maximises extent**w * n_kept over the\n'
        'coverage/sampling Pareto front. --min-keep first throws away every\n'
        'window below the epoch floor, then -w chooses among what survives:\n'
        'set the floor you can live with, then tune -w for taste.')
    keep.add_argument('-w', '--weight', type=float, default=1.0, metavar='W',
        help='extent exponent: 1 = balanced (default), >1 wider, <1 more epochs')
    keep.add_argument('--min-keep', type=float, default=0.0, metavar='F',
        help='hard floor on fraction of acquisitions kept, 0-1 (default 0)')
    keep.add_argument('--extent', type=float, nargs=2, metavar=('S', 'N'),
        default=None, help='force this window instead of solving for one')
    keep.add_argument('--no-extent', dest='do_extent', action='store_false',
        help='skip the keep-window entirely')

    parser.add_argument('-o', '--outfile', type=str, default='epochs_latlon.png',
        help='output figure (default: epochs_latlon.png)')
    parser.add_argument('--no-show', dest='show', action='store_false',
        help='save without opening a window')
    parser.add_argument('--no-shade', dest='shade', action='store_false',
        help='dashed window lines only, no grey band')
    parser.add_argument('-p', '--print-msg', action='store_true',
        help='print per-series spans and the coverage/sampling Pareto table')

    args = parser.parse_args()

    if args.names is not None and len(args.names) != len(args.file_paths):
        parser.error("The number of '--names' must match the number of 'file_paths'.")

    frames = []
    for i, file_path in enumerate(args.file_paths):
        if not os.path.exists(file_path):
            print(f"Error: File not found at '{file_path}'. Skipping.")
            continue

        df = read_file(file_path)
        if df.empty:
            print(f"Warning: No valid data parsed from '{file_path}'. Skipping.")
            continue

        if args.names is not None:
            df['source'] = args.names[i]
        else:
            df['source'] = os.path.splitext(os.path.basename(file_path))[0]
        frames.append(df)

    if not frames:
        print("No valid dataframes loaded. Exiting.")
        return

    data = pd.concat(frames, ignore_index=True)

    group_fields = pick_group_fields(data, args.group_by)
    groups = split_groups(data, group_fields)
    colors = assign_colors(groups, group_fields)

    print(f"grouping by: {group_fields if group_fields else ['(none)']}")
    for label, df in groups:
        n_pass = df['date'].nunique()
        print(f"  {label:<24s} {len(df):5d} frames, {n_pass:4d} acquisitions, "
              f"{df['date'].min()} - {df['date'].max()}")

    if args.print_msg:
        for label, df in groups:
            df_clean = df.dropna(subset=['datetime', 'south', 'north'])
            df_clean = merge_frames(df_clean) if args.merge else df_clean.sort_values('datetime')
            if df_clean.empty:
                print(f"\n[{label}] - No valid data to print.")
                continue
            print(f"\n[{label}]")
            print("Date        South       North")
            print("-----------------------------")
            for _, row in df_clean.iterrows():
                print(f"{row['date']}   {row['south']:.4f}   {row['north']:.4f}")
            print("-----------------------------")

    extents = solve_extents(data, args)
    for label, ext, _, _ in extents:
        print(extent_summary(label, ext))
        if args.print_msg and 'curve' in ext:
            extent_tradeoff_table(ext)

    plot_groups(groups, colors, extents=extents, outfile=args.outfile,
                merge=args.merge, show=args.show, shade=args.shade)


if __name__ == "__main__":
    main()
