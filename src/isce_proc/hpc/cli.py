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

from isce_proc.hpc import config

SCRIPTS = Path(__file__).resolve().parents[1] / 'contrib' / 'hpc_topsStack' / 'scripts'

EXAMPLE = """examples (from the stack directory, e.g. chile/a076/hpc_topsStack):
  topsstack.py show   ChileSenAT076.txt                  # resolved settings
  topsstack.py jobs   ChileSenAT076.txt                  # write run_files/*.job (+ helpers)
  topsstack.py submit ChileSenAT076.txt -s 17 -e 20      # submit steps 17-20 as an afterok chain
  topsstack.py status ChileSenAT076.txt 13-16            # which rows produced their outputs
  topsstack.py status ChileSenAT076.txt 15 --rerun --submit
  topsstack.py clean  ChileSenAT076.txt                  # kill-after table for this stack
  topsstack.py clean  ChileSenAT076.txt esd coreg_overlap --delete
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
    argv = ['-t', c.hpc.track, '--omp-topo', str(c.hpc.ompTopo), '--account', c.hpc.account, '--mail', c.hpc.mail]
    argv += ['--clean'] if c.hpc.clean else []
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


COMMANDS = {
    'show':   (cmd_show,   'print the settings resolved from the template'),
    'jobs':   (cmd_jobs,   'write Slurm job files into run_files/ (write_slurmJobs.py)'),
    'submit': (cmd_submit, 'submit job files as an afterok chain (submit_chained_dependencies.sh)'),
    'status': (cmd_status, 'check outputs per row, optionally rerun bad rows (check_topsStack.py)'),
    'clean':  (cmd_clean,  'delete intermediate files after their last reader (clean_topsStack.py)'),
}


def main(iargs=None):
    ap = argparse.ArgumentParser(prog='topsstack.py', description=__doc__, epilog=EXAMPLE,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True, metavar='command')
    for name, (_, helptext) in COMMANDS.items():
        p = sub.add_parser(name, help=helptext, description=helptext, add_help=False)
        p.add_argument('template', help='track template, e.g. ChileSenAT076.txt')
        p.add_argument('extra', nargs=argparse.REMAINDER, help='options passed to the underlying tool')
    args = ap.parse_args(iargs)
    c = config.load(args.template)
    rc = COMMANDS[args.cmd][0](c, args.extra)
    return rc if isinstance(rc, int) else 0
