#!/usr/bin/env python3
import re
import pandas as pd
from html import unescape
from datetime import datetime
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import itertools
import argparse
import os

# (Keep read_kml_latitudes and read_log_latitudes functions unchanged)
def read_kml_latitudes(kml_file):
    """Reads latitude span data from a KML file."""
    with open(kml_file, 'r') as f:
        content = f.read()

    placemarks = re.findall(r"<Placemark>(.*?)</Placemark>", content, re.DOTALL)
    rows = []

    for block in placemarks:
        name_match = re.search(r"<name>(.*?)</name>", block)
        name = name_match.group(1) if name_match else ""
        
        # More general date parsing: find any 8-digit sequence
        start_date_match = re.search(r'(\d{8})', name)
        start_date_str = start_date_match.group(1) if start_date_match else None

        coords_match = re.search(r"<coordinates>(.*?)</coordinates>", block, re.DOTALL)
        coords_str = coords_match.group(1).strip() if coords_match else ""
        lats = [float(c.split(',')[1]) for c in coords_str.split()] if coords_str else []
        
        south = min(lats) if lats else None
        north = max(lats) if lats else None

        # Add row only if essential data is found
        if start_date_str and lats:
            rows.append([
                start_date_str,
                datetime.strptime(start_date_str, '%Y%m%d'),
                south,
                north
            ])
            
    return pd.DataFrame(rows, columns=['date', 'datetime', 'south', 'north'])


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
                    rows.append([d, datetime.strptime(d, '%Y%m%d'), float(s), float(n)])
    return pd.DataFrame(rows, columns=['date', 'datetime', 'south', 'north'])


def plot_multiple_latitude_spans(dfs, labels):
    """Plots the latitude spans over time for multiple datasets."""
    color_cycle = itertools.cycle(['tab:blue', 'tab:orange', 'tab:green',
                                   'tab:red', 'tab:purple', 'tab:brown', 'tab:pink'])

    plt.rcParams.update({
        'font.size': 12,
        'axes.titlesize': 14,
        'axes.labelsize': 12,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10
    })

    fig, ax = plt.subplots(figsize=[10, 6])
    current_alpha = 0.9
    legend_elements = {}

    for df, label, color in zip(dfs, labels, color_cycle):
        # Sort DataFrame and drop NaNs once for the current track
        df_sorted = df.dropna(subset=['datetime', 'south', 'north']).sort_values('datetime')
        
        if df_sorted.empty:
            continue # Skip plotting if no valid data for this track

        # Plot all lines for the current track in one go
        # plt.vlines returns a LineCollection object, which can be directly used for legend
        line_collection = ax.vlines(x=df_sorted['datetime'], 
                                     ymin=df_sorted['south'], 
                                     ymax=df_sorted['north'],
                                     color=color, linewidth=2, alpha=current_alpha)
        
        # Store the LineCollection object for the legend
        legend_elements[label] = line_collection
        
        current_alpha -= 0.1
        if current_alpha < 0.3:
            current_alpha = 0.3

    # Generate legend from unique labels and their representative LineCollection objects
    # Note: For LineCollection, if it contains multiple lines, the label will appear once.
    # If you want a specific line *style* to appear, you might need to plot a single dummy line.
    ax.legend(handles=legend_elements.values(), labels=legend_elements.keys(), loc='best')

    ax.set_xlabel("Acquisition Time")
    ax.set_ylabel("Latitude (South to North)")
    ax.set_title("Latitude Span of Sentinel-1 Acquisitions Over Time")
    ax.grid(True, linestyle='--', alpha=0.6)


    # --- KEY MODIFICATIONS FOR DATE DISPLAY ---
    # 1. Set the major ticks to display months
    # You can choose MonthLocator(interval=1) for every month, or (interval=2) for every other, etc.
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))

    # 2. Set the minor ticks to display days
    #ax.xaxis.set_minor_locator(mdates.DayLocator(interval=15)) # Or DayLocator(interval=15) for every 15 day
    ax.xaxis.set_minor_locator(mdates.MonthLocator(interval=1)) # Or MonthLocator(interval=1) for every month

    # 3. Format the date string on the major ticks (e.g., "YYYY-MM-DD" or "Mon-DD")
    # '%Y-%m-%d' for year-month-day
    # '%b %d\n%Y' for "Jan 01\n2023" (month abbreviation, day, newline, year)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))

    # 4. Rotate x-axis labels for better readability if they overlap
    fig.autofmt_xdate()

    # --- END KEY MODIFICATIONS ---


    plt.tight_layout()
    plt.savefig('epochs_latlon.png', transparent=True, dpi=300, bbox_inches='tight')
    plt.show()


def main():
    """Main function to parse command-line arguments, read data, and plot latitude spans."""
    parser = argparse.ArgumentParser(
        description="Plot latitude spans of Sentinel-1 acquisitions from KML or log files.",
        formatter_class=argparse.RawTextHelpFormatter # For multiline help messages
    )
    parser.add_argument('file_paths', type=str, nargs='+',
        help='Path(s) to the input KML or log files. Example: path/to/file1.kml path/to/file2.log')
    parser.add_argument('-n', '--names', dest='names', type=str, nargs='*',
        help='Optional: Name(s) corresponding to each file. Example: "Track A" "Track B"'
    )
    parser.add_argument('-p', '--print-msg', action='store_true',
        help='If set, prints the parsed data in a tabular format to the console.'
    )

    args = parser.parse_args()

    file_paths  = args.file_paths
    track_names = args.names
    print_msg   = args.print_msg

    if track_names is not None and len(track_names) != len(file_paths):
        parser.error(
            "The number of '--track-names' must match the number of 'file_paths'."
        )

    if track_names is None:
        track_names = [f"Track {i+1}" for i in range(len(file_paths))]

    dfs = []
    loaded_track_names = []

    for i, file_path in enumerate(file_paths):
        if not os.path.exists(file_path):
            print(f"Error: File not found at '{file_path}'. Skipping.")
            continue
            
        if file_path.endswith('.kml'):
            df = read_kml_latitudes(file_path)
        elif file_path.endswith('.log'):
            df = read_log_latitudes(file_path)
        else:
            print(f"Error: Unsupported file type for '{file_path}'. Skipping.")
            continue
        
        if not df.empty:
            dfs.append(df)
            loaded_track_names.append(track_names[i])
        else:
            print(f"Warning: No valid data parsed from '{file_path}'. Skipping.")

    if not dfs:
        print("No valid dataframes loaded. Exiting.")
        return

    if print_msg:
        for df, label in zip(dfs, loaded_track_names):
            df_cleaned = df.dropna(subset=['datetime', 'south', 'north']).sort_values('datetime')
            if not df_cleaned.empty:
                print(f"\n[{label}]")
                print("Date        South       North")
                print("-----------------------------")
                for _, row in df_cleaned.iterrows():
                    print(f"{row['date']}   {row['south']:.4f}   {row['north']:.4f}")
                print("-----------------------------")
            else:
                print(f"\n[{label}] - No valid data to print.")
                
    plot_multiple_latitude_spans(dfs, loaded_track_names)


if __name__ == "__main__":
    main()

