#!/usr/bin/env python3
# One command for the Sentinel-1 topsStack workflow on Slurm; see `topsstack.py -h`.
import os
import sys

# use the isce_proc next to this file (e.g. ~/tools/isce-proc-v2), not another one on PYTHONPATH,
# here and in the scripts it runs (run_isce_stack.py imports isce_proc.utils)
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
os.environ['PYTHONPATH'] = os.pathsep.join([SRC] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p])

from isce_proc.hpc.cli import main

if __name__ == '__main__':
    sys.exit(main())
