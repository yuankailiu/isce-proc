"""topsstack.py: one command for the Sentinel-1 topsStack workflow on Slurm, driven by the track template.

Every subcommand takes the track template (e.g. ChileSenAT076.txt, in the stack directory) and
runs from that stack directory; options after the template are passed to the underlying tool.
"""
import argparse
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from isce_proc.utils.config import AUTO_DICT
from commands import config, data, export, ionqc, plot, report, stack

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'

EXAMPLE = """examples (from the stack directory, e.g. chile/a076/hpc_topsStack):
  topsstack.py search   ChileSenAT076.txt                # ASF search -> data/search_results.* (download runs it if needed)
  topsstack.py download ChileSenAT076.txt --slurm 8      # 8 parallel shards on compute nodes
  topsstack.py download ChileSenAT076.txt --dry-run      # what is missing
  topsstack.py download ChileSenAT076.txt --needed --verify   # only zips the stack reads, CRC-checked
  topsstack.py inspect  ChileSenAT076.txt                # s1_version.txt, epochs_latlon.png, ...
  topsstack.py select   ChileSenAT076.txt                # s1_select_ion.py (moves bad slices)
  topsstack.py dem      ChileSenAT076.txt                # DEM + water body
  topsstack.py stack  ChileSenAT076.txt                  # run_isce_stack.py: configs/, run_files/
  topsstack.py stack  ChileSenAT076.txt --ion-config     # filtIon/burstRampIon keys (was filtIon_config.sh)
  topsstack.py stack  ChileSenAT076.txt --select-pairs 5 10
  topsstack.py show   ChileSenAT076.txt                  # resolved settings
  topsstack.py jobs   ChileSenAT076.txt                  # write run_files/*.job (+ helpers)
  topsstack.py submit ChileSenAT076.txt -s 17 -e 20      # submit steps 17-20 as an afterok chain
  topsstack.py status ChileSenAT076.txt 13-16            # which rows produced their outputs
  topsstack.py status ChileSenAT076.txt 15 --rerun --submit
  topsstack.py clean  ChileSenAT076.txt                  # kill-after table for this stack
  topsstack.py clean  ChileSenAT076.txt esd coreg_overlap --delete
  topsstack.py report ChileSenAT076.txt                  # time/CPU/memory/cost/size per step
  topsstack.py ionqc  ChileSenAT076.txt --unw            # bad ion pairs after step 23 (--apply: exclude)
  topsstack.py plot   ChileSenAT076.txt ion              # or unw, baselines, network
  topsstack.py export ChileSenAT076.txt --dry-run        # copy to hpc.exportDir
"""


@contextmanager
def _in(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run_script_main(module, argv):
    """Run scripts/<module>.py main() with argv, as if called from the command line."""
    sys.path.insert(0, str(SCRIPTS))
    mod = __import__(module)
    old = sys.argv
    sys.argv = [f'{module}.py'] + list(argv)
    try:
        return mod.main()
    finally:
        sys.argv = old


def cmd_show(c, extra):
    print(config.show(c))


def cmd_jobs(c, extra):
    argv = ['-t', c.hpc.track, '--omp-topo', str(c.hpc.ompTopo), '--account', c.hpc.account, '--mail', c.hpc.mail,
            '--template', c.template]
    argv += ['--clean'] if c.hpc.clean else []
    argv += ['--gate', str(c.hpc.gateRetries)] if c.hpc.gate else []
    argv += ['--gpu-type', c.hpc.gpuType] + ([] if c.isce.useGPU else ['--no-gpu'])
    with _in(c.stack):
        return _run_script_main('write_slurmJobs', argv + list(extra))


def cmd_submit(c, extra):
    run_files = os.path.join(c.stack, 'run_files')
    if not os.path.isfile(os.path.join(run_files, 'submit_chained_dependencies.sh')):
        sys.exit('run_files/submit_chained_dependencies.sh not found: run `topsstack.py jobs` first')
    return subprocess.run(['bash', 'submit_chained_dependencies.sh', *extra], cwd=run_files).returncode


def cmd_status(c, extra):
    with _in(c.stack):
        return _run_script_main('check_topsStack', extra)


def cmd_clean(c, extra):
    with _in(c.stack):
        return _run_script_main('clean_topsStack', extra)


def cmd_search(c, extra):
    return data.search(c, extra)


def cmd_download(c, extra):
    ap = argparse.ArgumentParser(prog='topsstack.py download TEMPLATE')
    ap.add_argument('--needed', action='store_true', help='only the zips the stack VRTs read (reference/, secondarys/)')
    ap.add_argument('--verify', action='store_true', help='also CRC-check complete-size zips; re-download bad ones')
    ap.add_argument('--dry-run', action='store_true', help='only report what would be downloaded')
    ap.add_argument('--slurm', type=int, metavar='N', help=f'submit N Slurm array tasks, 1 CPU each (default: asf.shards = {AUTO_DICT["asf.shards"]})', nargs='?', const=0)
    ap.add_argument('--shard', type=str, metavar='I/N', help='this process handles every N-th file from I (used by --slurm)')
    ap.add_argument('-n', '--nproc', type=int, help=f'parallel downloads per process/task (default: asf.processes = {AUTO_DICT["asf.processes"]})')
    a = ap.parse_args(extra)
    if not (a.needed or a.shard) and not data.search_current(c):   # once here, never in the Slurm shards
        print('search_results.csv missing or from other asf.* settings: searching first', flush=True)
        data.search(c)
    if a.slurm is not None:
        flags = [f for f, on in (('--needed', a.needed), ('--verify', a.verify)) if on]
        flags += ['-n', str(a.nproc)] if a.nproc else []
        return data.download_slurm(c, a.slurm or c.asf.shards, flags)
    shard = tuple(map(int, a.shard.split('/'))) if a.shard else None
    return data.download(c, needed=a.needed, shard=shard, verify=a.verify, dry_run=a.dry_run, nproc=a.nproc)


def cmd_inspect(c, extra):
    return data.inspect(c, extra)


def cmd_select(c, extra):
    return data.select(c, extra)


def cmd_dem(c, extra):
    return data.dem(c, extra)


def cmd_stack(c, extra):
    ap = argparse.ArgumentParser(prog='topsstack.py stack TEMPLATE')
    ap.add_argument('--ion-config', action='store_true', help='set filtIon/burstRampIon config keys from ion.* (idempotent)')
    ap.add_argument('--select-pairs', nargs='*', type=int, metavar=('N', 'BRIDGE'),
                    help='thin steps 13-16 to N nearest pairs (+ one BRIDGE dates ahead) '
                         f'(default: select.numConnections = {AUTO_DICT["select.numConnections"]}, select.bridge = {AUTO_DICT["select.bridge"]})')
    a, rest = ap.parse_known_args(extra)
    if a.ion_config:
        return stack.ion_config(c)
    if a.select_pairs is not None:
        return stack.select_pairs(c, *a.select_pairs[:2])
    return stack.prep(c, rest)


def cmd_report(c, extra):
    return report.report(c, extra)


def cmd_export(c, extra):
    ap = argparse.ArgumentParser(prog='topsstack.py export TEMPLATE')
    ap.add_argument('--dry-run', action='store_true', help='only list what would be copied, with sizes')
    ap.add_argument('-n', '--nproc', type=int, default=8,
                    help='parallel rsync jobs; the interferograms are split over them (default: %(default)s)')
    a = ap.parse_args(extra)
    return export.export(c, dry_run=a.dry_run, nproc=a.nproc)


def cmd_ionqc(c, extra):
    ap = argparse.ArgumentParser(prog='topsstack.py ionqc TEMPLATE',
                                 description='closure test by default (after step 23); see commands/ionqc.py')
    ap.add_argument('--raw', action='store_true', help='also the raw-ionosphere spread test (possible after step 22)')
    ap.add_argument('--raw-only', action='store_true', help='only the raw test (after step 22, before 23)')
    ap.add_argument('--unw', action='store_true', help='also the correction test on the interferograms')
    ap.add_argument('--sample', type=int, default=0, help='--unw only on the flagged pairs + this many random ones; 0: all pairs (default)')
    ap.add_argument('--apply', action='store_true', help='add the excluded pairs to --exc_pair of run_24/run_26')
    ap.add_argument('-n', '--nproc', type=int, default=8, help='parallel processes (default: %(default)s)')
    ap.add_argument('--floor', type=float, default=3.0, help='minimum closure threshold [rad] (default: %(default)s)')
    ap.add_argument('--nmad', type=float, default=6.0, help='closure threshold = median + NMAD * MAD (default: %(default)s)')
    ap.add_argument('--raw-floor', type=float, default=50.0, help='minimum raw-spread threshold [rad] (default: %(default)s)')
    ap.add_argument('--raw-nmad', type=float, default=10.0, help='raw threshold = median + N * MAD (default: %(default)s)')
    ap.add_argument('--ratio', type=float, default=1.5, help='variance ratio at 100 km to list for checking (default: %(default)s)')
    a = ap.parse_args(extra)
    return ionqc.ionqc(c, raw=a.raw or a.raw_only, closure_test=not a.raw_only, unw=a.unw and not a.raw_only,
                       apply=a.apply, nproc=a.nproc, floor=a.floor, nmad=a.nmad, ratio_max=a.ratio,
                       raw_floor=a.raw_floor, raw_nmad=a.raw_nmad, sample=a.sample)


def cmd_plot(c, extra):
    if not extra or extra[0].startswith('-'):
        extra = ['ion'] + list(extra)                   # default: the ionosphere figures
    return plot.plot(c, extra[0], extra[1:])


COMMANDS = {
    'search':   (cmd_search,   'ASF search from asf.* -> data/search_results.csv/kml'),
    'download': (cmd_download, 'download/verify SLC zips (resumable; --slurm N for parallel shards)'),
    'inspect':  (cmd_inspect,  'SLC versions/starting ranges/slices and latitude extent (s1_version.py, s1_kml_latlon.py)'),
    'select':   (cmd_select,   'topsStack s1_select_ion.py: move unusable slices to not_used/'),
    'dem':      (cmd_dem,      'DEM and water body over dem.snwe (download_dem.sh)'),
    'stack':    (cmd_stack,    'run files (run_isce_stack.py); --ion-config; --select-pairs N [BRIDGE]'),
    'show':   (cmd_show,   'print the settings resolved from the template'),
    'jobs':   (cmd_jobs,   'write Slurm job files into run_files/ (write_slurmJobs.py)'),
    'submit': (cmd_submit, 'submit job files as an afterok chain (submit_chained_dependencies.sh)'),
    'status': (cmd_status, 'check outputs per row, optionally rerun bad rows (check_topsStack.py)'),
    'clean':  (cmd_clean,  'delete intermediate files after their last reader (clean_topsStack.py)'),
    'report': (cmd_report, 'per-step time, CPU, memory, cost, disk use (sacct) -> logs/report_<date>.*'),
    'ionqc':  (cmd_ionqc,  'flag bad ionosphere pairs (loop closure; --unw: correction test) before step 24'),
    'plot':   (cmd_plot,   'quick-look figures into pic/: ion, unw, baselines, network'),
    'export': (cmd_export, 'copy products and records to hpc.exportDir (rsync; --dry-run)'),
}


SCRIPT_OF = {'status': 'check_topsStack', 'clean': 'clean_topsStack', 'jobs': 'write_slurmJobs'}

# template keys each command reads (printed with their defaults by `CMD -h`)
KEYS_OF = {'search': ('asf.',), 'download': ('asf.',), 'inspect': ('asf.dataDir',),
           'select': ('asf.dataDir', 'select.southNorth', 'select.minAcq'), 'dem': ('dem.', 'isce.demFile'),
           'stack': ('isce.', 'select.numConnections', 'select.bridge', 'ion.'), 'jobs': ('hpc.',),
           'submit': ('hpc.gate',), 'report': ('hpc.costPerCpuHour',), 'ionqc': ('isce.paramIonFile',),
           'plot': ('hpc.track',), 'export': ('hpc.exportDir',), 'show': ('',)}


def _keys_help(cmd):
    keys = [k for k in AUTO_DICT if any(k.startswith(p) for p in KEYS_OF.get(cmd, ()))]
    if keys:
        print(f'\ntemplate keys read (default; None = derived from isce.* or the template name, see `show`):')
        w = max(map(len, keys))
        for k in keys:
            print(f'  {k:<{w}} = {AUTO_DICT[k]}')


def _help(cmd):
    print(f'topsstack.py {cmd} TEMPLATE [options]: {COMMANDS[cmd][1]}\n')
    if cmd in SCRIPT_OF:                                   # options go to this script
        try:
            _run_script_main(SCRIPT_OF[cmd], ['-h'])
        except SystemExit:
            pass
    elif cmd == 'submit':                                  # the options are in the script header
        sh = SCRIPTS / 'submit_chained_dependencies.sh'
        head = []
        for l in open(sh).readlines()[1:]:
            if l.startswith('# TODO') or (head and not l.startswith('#')):
                break
            if l.startswith('#'):
                head.append(l[1:])
        print(''.join(head).rstrip())
    else:
        try:
            COMMANDS[cmd][0](None, ['-h'])                 # commands with their own argparse print and exit
        except SystemExit:
            pass
        except Exception:
            print('options after TEMPLATE are passed to the underlying tool; see README.md')
    _keys_help(cmd)
    return 0


def main(iargs=None):
    ap = argparse.ArgumentParser(prog='topsstack.py', description=__doc__, epilog=EXAMPLE,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True, metavar='command')
    for name, (_, helptext) in COMMANDS.items():
        p = sub.add_parser(name, help=helptext, description=helptext, add_help=False)
        p.add_argument('template', help='track template, e.g. ChileSenAT076.txt')
        p.add_argument('extra', nargs=argparse.REMAINDER, help='options passed to the underlying tool')
    argv = sys.argv[1:] if iargs is None else list(iargs)
    if len(argv) >= 2 and argv[0] in COMMANDS and argv[1] in ('-h', '--help'):
        return _help(argv[0])                              # topsstack.py CMD -h: the command's own options
    args = ap.parse_args(argv)
    c = config.load(args.template)
    rc = COMMANDS[args.cmd][0](c, args.extra)
    return rc if isinstance(rc, int) else 0
