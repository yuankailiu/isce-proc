#!/usr/bin/env python3
import argparse
import csv
import datetime
import glob
import os
import xml.etree.ElementTree as ET
import zipfile

import asf_search as asf

SEARCH_GRANULES_FILE = "search_results.csv"
FAILED_LIST_FILE     = "failed_list.txt"
HISTORY_MARKER        = "# MENDED on"


# ==============================================================
# Utility Functions
# ==============================================================

def decide_nproc(n_granules, max_safe=16):
    """
    Decide optimal parallel download processes.

    Parameters
    ----------
    n_granules : int
        Number of scenes/granules to download
    max_safe : int, optional
        Hard cap for ASF/CMR friendliness (default=16)

    Returns
    -------
    int
        Suggested number of processes
    """
    cpu = os.cpu_count() or 1
    nproc = min(n_granules, 2*cpu, max_safe)
    return max(1, nproc)


def extract_granules_from_log(logfile):
    """Extract S1 granule names from a log file, strip .zip"""
    granules = []
    start = []
    with open(logfile, 'r') as f:
        for line in f:
            if line.startswith('checking small-number'):
                start.append(True); continue

            if start == [True]:
                if line.startswith('slice'):
                    start.append(True); continue

            if start == [True, True]:
                if line.startswith('S1'):
                    match = line.split()[0].split('.zip')[0]
                    granules.append(match)
    return sorted(set(granules))


def read_granules_from_txt(txtfile):
    """Read granules from a plain text file"""
    with open(txtfile, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


def save_granules_to_txt(granules, outfile):
    """Save granule names to text file"""
    with open(outfile, 'w') as f:
        for g in granules:
            f.write(g + "\n")


# ==============================================================
# Sentinel-1 SAFE parsing class
# ==============================================================

class sentinelSLC(object):
    def __init__(self, safe_file):
        self.safe_file = safe_file

    def get_datetime(self):
        datefmt = "%Y%m%dT%H%M%S"
        safe = os.path.basename(self.safe_file)
        fields = safe.split('_')
        self.platform = fields[0]
        self.start_date_time = datetime.datetime.strptime(fields[5], datefmt)
        self.stop_date_time = datetime.datetime.strptime(fields[6], datefmt)
        self.date = (self.start_date_time.date()).isoformat().replace('-','')

    def get_param(self):
        c = 299792458.0
        with zipfile.ZipFile(self.safe_file, 'r') as zf:
            manifest = [item for item in zf.namelist() if '.SAFE/manifest.safe' in item][0]
            xmlstr = zf.read(manifest)
            root = ET.fromstring(xmlstr)
            elem = root.find('.//metadataObject[@ID="processing"]')

            nsp = "{http://www.esa.int/safe/sentinel-1.0}"
            rdict = elem.find('.//xmlData/' + nsp + 'processing/' + nsp + 'facility').attrib
            self.proc_site = rdict['site'] +', '+ rdict['country']

            rdict = elem.find('.//xmlData/' + nsp + 'processing/' + nsp + 'facility/' + nsp + 'software').attrib
            self.proc_version = rdict['version']

            anna = sorted([item for item in zf.namelist() if '.SAFE/annotation/s1' in item])
            if len(anna) == 6:
                anna = anna[1:6:2]

            startingRange = []
            for k in range(3):
                xmlstr = zf.read(anna[k])
                root = ET.fromstring(xmlstr)
                startingRange.append(float(root.find('imageAnnotation/imageInformation/slantRangeTime').text)*c/2.0)

            self.startingRanges = startingRange


# ==============================================================
# Failed-list management
# ==============================================================

def load_failed_list():
    """
    Load failed_list.txt.
    Returns:
        history_lines: lines to preserve (comments, markers, unfixed failures)
        mendable: scene IDs we can attempt to fix
    """
    if not os.path.exists(FAILED_LIST_FILE):
        return [], []

    raw_lines = open(FAILED_LIST_FILE).read().splitlines()
    history_lines = []
    mendable = []

    for line in raw_lines:
        if not line.strip():
            history_lines.append(line)
            continue

        if line.startswith(HISTORY_MARKER):
            history_lines.append(line)
            continue

        # This is a failed scene ID
        scene_id = line.replace(".zip", "")
        mendable.append(scene_id)
        history_lines.append(scene_id)

    return history_lines, mendable


def check_zip_files():
    """Check all .zip files and return a list of failures"""
    zips = sorted(glob.glob('./S1*_IW_SLC_*.zip'))
    failed = []

    for z in zips:
        try:
            safeObj = sentinelSLC(z)
            safeObj.get_datetime()
            safeObj.get_param()
            print(f"OK: {os.path.basename(z)}")
        except Exception as e:
            print(f"FAIL: {os.path.basename(z)} - {str(e)}")
            failed.append(os.path.basename(z).replace('.zip', ''))

    if failed:
        with open(FAILED_LIST_FILE, 'w') as f:
            for fn in failed:
                f.write(fn + '\n')
        print(f"New failed list written to {FAILED_LIST_FILE}")
    else:
        print("All .zip files passed. Nothing to fix.")

    return failed


def recheck_and_update_failed_list(scene_ids, history_lines=[]):
    """
    After re-downloading, recheck scenes and update failed_list.txt.
    """
    fixed = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    with open(FAILED_LIST_FILE, 'w') as f:
        for line in history_lines:
            f.write(line + '\n')

        for sid in scene_ids:
            try:
                safeObj = sentinelSLC(f"{sid}.zip")
                safeObj.get_datetime()
                safeObj.get_param()
                fixed.append(sid)
                print(f"RECHECK OK: {sid}")
                f.write(f"{HISTORY_MARKER} {now}: {sid}\n")
            except Exception:
                print(f"RECHECK FAIL: {sid}")
                if sid in history_lines:
                    # Already in failed history, now still failed; do not duplicate
                    continue
                else:
                    f.write(f"{sid}\n")

    print(f"{len(fixed)} scenes fixed and marked.")


# ==============================================================
# Downloader
# ==============================================================

def redownload_scenes(scene_ids, kill=False):
    print(f"Preparing to re-download {len(scene_ids)} scenes...")
    for sid in scene_ids:
        zipfile_path = f"{sid}.zip"
        if os.path.exists(zipfile_path):
            if kill:
                os.remove(zipfile_path)
                print(f"Deleted existing: {zipfile_path}")
            else:
                print(f" -> You do not kill existing: {zipfile_path}")
                continue

    with open(os.path.expanduser("~/.netrc"), "r") as f:
        fields = f.read().split()
        username = fields[fields.index("login") + 1]
        password = fields[fields.index("password") + 1]

    session = asf.ASFSession().auth_with_creds(username, password)
    granules = asf.granule_search(scene_ids)

    nproc = decide_nproc(len(scene_ids))
    print(f"Downloading {len(scene_ids)} scenes with {nproc} parallel processes")

    granules.download(path=".", session=session, processes=nproc)

    print("End of downloading")
    os.system('rm -rf *.iso.xml')


# ==============================================================
# Main
# ==============================================================

def main():
    parser = argparse.ArgumentParser(description="ASF mendzip script with optional log/txt mode.")
    parser.add_argument("--log", help="Path to log file to extract granule names from.")
    parser.add_argument("--txt", help="Path to plain text file of granule names.")
    parser.add_argument("--kill", help="Delete existing files.", action='store_true', default=False)

    args = parser.parse_args()

    # Special mode: from log or txt
    if args.log or args.txt:
        if args.log:
            granules = extract_granules_from_log(args.log)
            print(f"Found {len(granules)} granules from {args.log} file.")
            save_granules_to_txt(granules, 's1_select_ion.txt')

        if args.txt:
            granules = read_granules_from_txt(args.txt)
            print(f"Found {len(granules)} granules from {args.txt} file.")

        redownload_scenes(granules, kill=args.kill)
        recheck_and_update_failed_list(granules, [])
        return

    # Normal mode
    if os.path.exists(SEARCH_GRANULES_FILE):
        print(f"{SEARCH_GRANULES_FILE} found. Checking missing files...")
        expected_granules = [row['Granule Name'] for row in csv.DictReader(open(SEARCH_GRANULES_FILE))]
        existing = set(os.path.basename(f).replace(".zip","") for f in glob.glob("*.zip"))
        missing = sorted(set(expected_granules) - existing)
    else:
        missing = []

    history_lines, mendable_from_history = load_failed_list()

    if not history_lines:
        print("No failed_list.txt found. Running fresh check.")
        mendable = check_zip_files()
        history_lines = []   # start with empty history
    else:
        mendable = mendable_from_history

    mendable = sorted(set(mendable) | set(missing))

    if not mendable:
        print("No mendable scenes found. Exiting.")
        return

    print(f"Found {len(mendable)} scenes to re-download.")
    redownload_scenes(mendable, kill=args.kill)
    recheck_and_update_failed_list(mendable, history_lines)


if __name__ == '__main__':
    main()
