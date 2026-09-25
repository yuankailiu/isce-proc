#!/usr/bin/env python3
# One command for the Sentinel-1 topsStack workflow on Slurm; see `topsstack.py -h`.
import os
import sys

# use the code next to this file (commands/ here, isce_proc.utils in src/, e.g. of
# ~/tools/isce-proc-v2), not another isce_proc on PYTHONPATH; also in the scripts it runs
# (run_isce_stack.py imports isce_proc.utils)
HERE = os.path.dirname(os.path.abspath(__file__))                           # contrib/hpc_topsStack
SRC = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))               # src
sys.path[:0] = [HERE, SRC]
os.environ['PYTHONPATH'] = os.pathsep.join([SRC] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p])

from commands.cli import main

if __name__ == '__main__':
    sys.exit(main())
