#!/usr/bin/env python3
# One command for the Sentinel-1 topsStack workflow on Slurm; see `topsstack.py -h`.
import os
import sys

# use the isce_proc next to this file (e.g. ~/tools/isce-proc-v2), not another one on PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from isce_proc.hpc.cli import main

if __name__ == '__main__':
    sys.exit(main())
