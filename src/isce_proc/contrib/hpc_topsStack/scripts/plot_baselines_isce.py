#!/usr/bin/env python3

import os
import glob
import argparse
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

# --- Helper Functions ---
def _check_network_connectivity(pairs): # Removed bad_pairs from here
    """Placeholder for network connectivity check. Always returns True for now."""
    return True

def _parse_baseline_value(line):
    """Extracts the Bperp value from a line, robustly handling formatting."""
    try:
        return float(line.split(':')[1].strip().split(' ')[0])
    except (ValueError, IndexError) as e:
        print(f"Warning: Failed to parse 'Bperp' from line '{line.strip()}'. Error: {e}")
        return None

# --- Main Plotting Function ---
def plot_baseline_network(baseline_data_dir, output_plot_dir, track_id, drop_dates=None, show_date_labels=False):
    """
    Generates and saves a baseline network plot.

    Automatically identifies sequential interferometric pairs from directory names
    and plots their relative perpendicular baselines.

    Args:
        baseline_data_dir (str): Path to the directory containing baseline files.
                                 Expected: `yyyymmdd_yyyymmdd/yyyymmdd_yyyymmdd.txt`.
        output_plot_dir (str): Directory to save the generated PDF plot.
        track_id (str): Identifier for the radar track (e.g., 'Track007').
        drop_dates (list, optional): List of 'yyyymmdd' strings for dates to mark in crimson.
        show_date_labels (bool, optional): If True, display date labels next to scatter points. Defaults to False.
    """
    drop_dates = drop_dates or [] # Ensure it's a list even if None is passed

    # 1. Discover all unique dates and infer sequential pairs
    potential_pair_dirs = sorted(glob.glob(os.path.join(baseline_data_dir, '????????_????????')))

    pair_dirs = []
    all_dates = set()

    for d_path in potential_pair_dirs:
        dir_name = os.path.basename(d_path)
        parts = dir_name.split('_')

        # Strict check for exactly two parts, both 8 digits long and composed only of digits
        if len(parts) == 2 and len(parts[0]) == 8 and len(parts[1]) == 8 and parts[0].isdigit() and parts[1].isdigit():
            pair_dirs.append(d_path)
            all_dates.update(parts)
        else:
            print(f"Skipping non-standard date directory: {dir_name}")

    sorted_unique_dates = sorted(list(all_dates))
    sequential_pairs = [f"{sorted_unique_dates[i]}_{sorted_unique_dates[i+1]}" for i in range(len(sorted_unique_dates) - 1)]

    if not sequential_pairs:
        print(f"Error: No sequential pairs formed from valid dates in '{baseline_data_dir}'. Check data.")
        return

    # 2. Read baseline values for all pairs relative to the earliest date
    reference_date = sorted_unique_dates[0]
    baseline_values = {}
    baseline_values[(reference_date, reference_date)] = 0.0 # Baseline of ref_date to itself is 0

    for d_path in pair_dirs:
        date1, date2 = os.path.basename(d_path).split('_')
        baseline_file_path = os.path.join(d_path, f"{date1}_{date2}.txt")

        if not os.path.exists(baseline_file_path):
            continue

        with open(baseline_file_path) as f_handle:
            b_perps = [_parse_baseline_value(line) for line in f_handle if line.strip().startswith('Bperp')]
            valid_b_perps = [b for b in b_perps if b is not None]
            if valid_b_perps:
                baseline_values[(date1, date2)] = np.mean(valid_b_perps)

    # 3. Plot the baseline network
    fig, ax = plt.subplots(figsize=(12, 6))

    dates_for_plot_points = set()

    for pair in sequential_pairs:
        date1_str, date2_str = pair.split('_')

        try:
            dt1, dt2 = datetime.strptime(date1_str, '%Y%m%d'), datetime.strptime(date2_str, '%Y%m%d')
        except ValueError:
            print(f"Invalid date format for pair '{pair}'. Skipping.")
            continue

        bp1 = baseline_values.get((reference_date, date1_str), 0.0)
        bp2 = baseline_values.get((reference_date, date2_str), 0.0)

        # Lines are always black/solid now, as 'bad_pairs' logic is removed
        ax.plot([dt1, dt2], [bp1, bp2], linestyle='-', color='k', alpha=0.8, zorder=1)
        dates_for_plot_points.update([date1_str, date2_str])

    # Plot individual date points
    scatter_dates_sorted = sorted(list(dates_for_plot_points))
    scatter_datetimes = [datetime.strptime(d, '%Y%m%d') for d in scatter_dates_sorted]
    scatter_baselines = [baseline_values.get((reference_date, d), 0.0) for d in scatter_dates_sorted]

    # Determine colors for scatter points
    scatter_colors = ['crimson' if d in drop_dates else 'C0' for d in scatter_dates_sorted]

    ax.scatter(scatter_datetimes, scatter_baselines, s=50, c=scatter_colors, zorder=2)

    # Add date labels if requested
    if show_date_labels:
        for i, date_str in enumerate(scatter_dates_sorted):
            ax.annotate(
                datetime.strptime(date_str, '%Y%m%d').strftime('%Y%m%d'), # Display full YYYYMMDD
                (scatter_datetimes[i], scatter_baselines[i]),
                textcoords="offset points",
                xytext=(0, 10), # Offset text above the point
                ha='center',
                fontsize=8,
                color='darkgray' if date_str in drop_dates else 'black' # Different color for dropped dates' labels
            )


    # 4. Customize and save the plot
    #network_status = _check_network_connectivity(sequential_pairs)
    # The common_bad_pairs_flag is no longer relevant for a title about bad pairs,
    # so adapting this text.
    ax.set_title(f"Baseline history of SLCs {track_id}")
    ax.set_xlabel("Acquisition Date")
    ax.set_ylabel("Relative Perpendicular Baseline (m)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True)
    fig.autofmt_xdate()
    fig.tight_layout()

    os.makedirs(output_plot_dir, exist_ok=True)
    output_filename = os.path.join(output_plot_dir, f'baseline_network_{track_id}.pdf') # Simplified filename
    fig.savefig(output_filename)
    plt.close()
    print(f"Baseline network plot saved to: {output_filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="""Generate a baseline network plot from InSAR baseline files.

        The script automatically identifies sequential interferometric pairs
        based on the 'yyyymmdd_yyyymmdd' directory structure and plots
        their perpendicular baselines relative to the earliest acquisition date.
        """,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--dir",
        dest="baseline_data_dir",
        type=str,
        required=True,
        help="Root directory containing baseline pair folders (e.g., 'data/baselines/')."
    )
    parser.add_argument(
        "--out",
        dest="output_plot_dir",
        type=str,
        required=True,
        help="Directory to save the generated PDF plot (e.g., 'output/plots/')."
    )
    parser.add_argument(
        "--name",
        dest="track_id",
        type=str,
        required=True,
        help="A unique identifier for the radar track (e.g., 'Track007', 'Ascending_123')."
    )
    parser.add_argument(
        "--drop-date",
        dest="drop_dates",
        nargs='*', # Allows zero or more arguments
        default=[],
        help="Optional: List of 'yyyymmdd' strings for dates to be marked in crimson red on the scatter plot."
    )
    parser.add_argument(
        "--show-date-labels",
        action="store_true", # This flag will be True if present
        help="Set this flag to display 'yyyymmdd' labels next to each scatter point."
    )

    args = parser.parse_args()

    plot_baseline_network(
        baseline_data_dir=args.baseline_data_dir,
        output_plot_dir=args.output_plot_dir,
        track_id=args.track_id,
        drop_dates=args.drop_dates,
        show_date_labels=args.show_date_labels
    )
