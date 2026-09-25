"""Data side of topsstack.py: search, download, inspect, select, dem.

All settings come from the track template (asf.*, dem.*, select.*; see commands/config.py).
Outputs go to the data directory (asf.dataDir, default ../data next to the stack), which the
stack's SLC/ links to.
"""
import csv
import glob
import netrc
import os
import re
import shlex
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

HPC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # contrib/hpc_topsStack
PKG = os.path.dirname(os.path.dirname(HPC_DIR))                             # src/isce_proc
SCRIPTS = os.path.join(HPC_DIR, 'scripts')
TOPSSTACK = os.path.join(HPC_DIR, 'topsstack.py')


def _log(c, name):
    os.makedirs(os.path.join(c.stack, 'logs'), exist_ok=True)
    return open(os.path.join(c.stack, 'logs', f'{name}_{datetime.now():%Y-%m-%d}.log'), 'a')


def _run(cmd, cwd, log, stdout=None):
    """Run a command, echo it to screen and log; return its exit code."""
    line = ' '.join(shlex.quote(x) for x in cmd)
    print(f'>> ({cwd}) {line}', flush=True)
    log.write(f'## {datetime.now():%F %T} ({cwd}) {line}\n')
    log.flush()
    return subprocess.run(cmd, cwd=cwd, stdout=stdout).returncode


# ---------------------------------------------------------------- search / download
def _session():
    """ASF session from ~/.netrc (Earthdata token as the password, or login/password)."""
    import asf_search as asf
    login, _, password = netrc.netrc().authenticators('urs.earthdata.nasa.gov')
    if login in ('token', '') or len(password) > 200:           # an EDL token stored as password
        return asf.ASFSession().auth_with_token(password)
    return asf.ASFSession().auth_with_creds(login, password)


def search(c, extra=()):
    """ASF search from asf.*; write search_results.csv/kml in the data dir."""
    import asf_search as asf
    if not (c.asf.wkt and c.asf.relativeOrbit and c.asf.flightDirection):
        sys.exit('need asf.wkt (or isce.boundingBox), asf.relativeOrbit and asf.flightDirection')
    platforms = {'S1A': asf.PLATFORM.SENTINEL1A, 'S1B': asf.PLATFORM.SENTINEL1B,
                 'S1C': getattr(asf.PLATFORM, 'SENTINEL1C', 'Sentinel-1C')}
    res = _cmr(lambda: asf.geo_search(platform=[platforms[p] for p in c.asf.platforms], intersectsWith=c.asf.wkt,
                                      start=c.asf.start, end=c.asf.end, processingLevel=asf.PRODUCT_TYPE.SLC,
                                      beamMode=asf.BEAMMODE.IW, relativeOrbit=c.asf.relativeOrbit,
                                      flightDirection=c.asf.flightDirection))
    os.makedirs(c.data, exist_ok=True)
    with open(os.path.join(c.data, 'search_results.csv'), 'w') as f:
        f.writelines(res.csv())
    with open(os.path.join(c.data, 'search_results.kml'), 'w') as f:
        f.writelines(res.kml())
    gb = sum(r.properties.get('bytes') or 0 for r in res) / 1e9
    with _log(c, 'search') as log:
        log.write(f'## {datetime.now():%F %T} {len(res)} SLC products, {gb:.0f} GB; wkt={c.asf.wkt} '
                  f'orbit={c.asf.relativeOrbit} {c.asf.flightDirection} {c.asf.platforms} {c.asf.start}..{c.asf.end}\n')
    print(f'{len(res)} SLC products ({gb:.0f} GB) -> {c.data}/search_results.csv/kml')


def _wanted(c, needed):
    """Granule names to have in the data dir: all search results, or only those the stack's VRTs read."""
    if needed:
        names = set()
        for v in glob.glob(os.path.join(c.stack, 'reference', 'IW*', 'burst_[0-9][0-9].slc.vrt')) + \
                 glob.glob(os.path.join(c.stack, 'secondarys', '*', 'IW*', 'burst_[0-9][0-9].slc.vrt')):
            try:                                   # only unpacked bursts; skip files vanishing meanwhile
                names |= set(re.findall(r'/([^/]+)\.zip/', open(v).read()))
            except FileNotFoundError:
                pass
        if not names:
            sys.exit('--needed: no VRTs reading SLC zips found under reference/ or secondarys/')
        return sorted(names)
    f = os.path.join(c.data, 'search_results.csv')
    if not os.path.isfile(f):
        sys.exit(f'{f} not found: run `topsstack.py search` first')
    return sorted({r['Granule Name'] for r in csv.DictReader(open(f))})


def _cmr(query, tries=3, timeout=120):
    """Run an asf_search query with a longer CMR timeout and retries (CMR is often slow)."""
    import time
    import asf_search as asf
    asf.constants.INTERNAL.CMR_TIMEOUT = max(asf.constants.INTERNAL.CMR_TIMEOUT, timeout)
    for attempt in range(tries):
        try:
            return query()
        except asf.ASFSearchError as e:
            if attempt == tries - 1:
                raise SystemExit(f'CMR/ASF search failed {tries} times, last: {e}')
            print(f'CMR query retry {attempt + 1}: {e}', flush=True)
            time.sleep(15 * (attempt + 1))


def _granules(names, batch=50):
    """SLC products for granule names, in small batches."""
    import asf_search as asf
    return [r for k in range(0, len(names), batch)
            for r in _cmr(lambda: asf.granule_search(names[k:k + batch])) if r.properties['processingLevel'] == 'SLC']


def _crc_ok(path):
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


def _catalog(c):
    """name -> (fileName, bytes, url) from every search_results.csv under the data dir.

    'Size (MB)' is MiB at full precision, so bytes are exact; no CMR query is needed.
    """
    cat = {}
    for f in glob.glob(os.path.join(c.data, '**', 'search_results.csv'), recursive=True):
        for r in csv.DictReader(open(f)):
            if r.get('Processing Level', 'SLC') == 'SLC' and r.get('URL'):
                cat[r['Granule Name']] = (os.path.basename(r['URL']), round(float(r['Size (MB)']) * 1048576), r['URL'])
    return cat


def download(c, needed=False, shard=None, verify=False, dry_run=False, nproc=None):
    """Download missing/incomplete zips; resumable, retries, optional CRC check."""
    import asf_search as asf
    names = _wanted(c, needed)
    if shard:
        i, n = shard
        names = names[i::n]
    cat = _catalog(c)
    unknown = [x for x in names if x not in cat]
    if unknown:                                                  # not in any search_results.csv: ask CMR
        print(f'{len(unknown)} names not in search_results.csv; querying CMR', flush=True)
        for r in _granules(unknown):
            p = r.properties
            cat[p['fileName'][:-4]] = (p['fileName'], int(p['bytes']), p['url'])
    items = [cat[x] for x in names if x in cat]
    path = lambda it: os.path.join(c.data, it[0])
    ok = lambda it: os.path.exists(path(it)) and os.path.getsize(path(it)) == it[1]
    todo = [it for it in items if not ok(it)]
    if verify:
        have = [it for it in items if ok(it)]
        with ThreadPoolExecutor(nproc or c.asf.processes) as ex:
            bad = [it for it, good in zip(have, ex.map(lambda it: _crc_ok(path(it)), have)) if not good]
        print(f'CRC check: {len(bad)} of {len(have)} complete-size zips fail')
        todo += bad
    gb = sum(it[1] for it in todo) / 1e9
    print(f'{len(names)} wanted, {len(items)} with a download URL, {len(todo)} to download ({gb:.0f} GB)', flush=True)
    not_found = sorted(set(names) - {it[0][:-4] for it in items})
    for m in not_found:
        print(f'NOT FOUND {m}')
    if dry_run or not todo:
        return 0
    import socket
    socket.setdefaulttimeout(300)       # asf download calls have no read timeout: a stalled transfer
    session = _session()                # would hang the thread for good (seen 2026-09-23, 19 h)

    def get(it):
        name, size, url = it
        for attempt in range(3):
            if os.path.exists(path(it)):
                os.remove(path(it))                              # partial or corrupt
            try:
                asf.download_url(url=url, path=c.data, filename=name, session=session)
            except Exception as e:
                print(f'RETRY {attempt} {name}: {e}', flush=True)
            if ok(it) and (not verify or _crc_ok(path(it))):
                print(f'OK {name}', flush=True)
                return True
        print(f'FAILED {name}', flush=True)
        return False

    with ThreadPoolExecutor(nproc or c.asf.processes) as ex:
        n_ok = sum(ex.map(get, todo))
    left = [it[0] for it in items if not ok(it)]
    print(f'done: {n_ok}/{len(todo)} downloaded; {len(left)} still missing/incomplete')
    with _log(c, 'download') as log:
        log.write(f'## {datetime.now():%F %T} shard={shard} wanted={len(names)} downloaded={n_ok}/{len(todo)} '
                  f'left={len(left)} not_found={len(not_found)}\n' + ''.join(f'LEFT {x}\n' for x in left))
    return 1 if left else 0


def download_slurm(c, n, flags):
    """Submit `download` as an n-task Slurm array (one shard per task, separate nodes)."""
    job = os.path.join(c.data, 'download.sbatch')
    with open(job, 'w') as f:
        f.write(f"""#!/bin/bash
#SBATCH -A {c.hpc.account}
#SBATCH -J download_{c.hpc.track}
#SBATCH --array=0-{n - 1}
#SBATCH --partition=expansion
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 24:00:00
#SBATCH --output={c.data}/download-%A_%a.out
# one shard per task; resumable: re-submit the same file to continue
{sys.executable} {TOPSSTACK} download {c.template} --shard ${{SLURM_ARRAY_TASK_ID}}/{n} {' '.join(flags)}
""")
    out = subprocess.run(['sbatch', '--parsable', job], capture_output=True, text=True)
    if out.returncode:
        sys.exit(out.stderr.strip())
    print(f'submitted {job}: job {out.stdout.strip()} ({n} shards); logs {c.data}/download-*.out')


# ---------------------------------------------------------------- inspect / select / dem
def inspect(c, extra=()):
    """s1_version.txt, s1_slice.txt (s1_version.py); epochs_latlon.png, south_north_extent.txt (s1_kml_latlon.py)."""
    with _log(c, 'inspect') as log:
        rc = _run([sys.executable, os.path.join(SCRIPTS, 's1_version.py'), '-d', c.data, '-o', c.data], c.data, log)
        kml = os.path.join(c.data, 'search_results.kml')
        if os.path.isfile(kml):
            with open(os.path.join(c.data, 'south_north_extent.txt'), 'w') as out:
                rc |= _run([sys.executable, os.path.join(SCRIPTS, 's1_kml_latlon.py'), kml,
                            '-o', os.path.join(c.data, 'epochs_latlon.png'), '--no-show', *extra], c.data, log, stdout=out)
            print(open(os.path.join(c.data, 'south_north_extent.txt')).read())
        else:
            print(f'{kml} not found: skipping the latitude-extent plot')
    return rc


def select(c, extra=()):
    """topsStack s1_select_ion.py on the data dir (moves unusable slices to not_used/)."""
    if not c.select.southNorth:
        sys.exit('need select.southNorth or isce.boundingBox')
    s, n = c.select.southNorth
    with _log(c, 'select') as log, open(os.path.join(c.data, 's1_select_ion.txt'), 'w') as out:
        return _run(['s1_select_ion.py', '-dir', c.data, '-sn', str(s), str(n), '-nr', str(c.select.minAcq), *extra],
                    c.data, log, stdout=out)


def dem(c, extra=()):
    """DEM (1 and 3 arcsec) and 1-arcsec water body over dem.snwe (download_dem.sh)."""
    if not c.dem.snwe:
        sys.exit('need dem.snwe or isce.boundingBox')
    os.makedirs(c.dem.dir, exist_ok=True)
    with _log(c, 'dem') as log:
        return _run(['bash', os.path.join(HPC_DIR, 'download_dem.sh'), *map(str, c.dem.snwe)], c.dem.dir, log)
