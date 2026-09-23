# Setup to submit stackSentinel batch to Slurm on HPC
# Author: Yuan-Kai Liu, Oliver Stephenson, April 2023
#               originally from stack_sentinel_cmd.sh
#
# First, run `run_isce_stack.py` (https://github.com/earthdef/sar-proc/tree/main/tools)
# Once you have all the run_files, run this shell to generate slurm jobs.
# All the work (copying helpers into run_files/, writing *.job, the disk-usage job and
# the deletion lines) is done by scripts/write_slurmJobs.py; see its --help.

# Edit below
############################
# SAR track e.g., a087, AT087, SenAT087; as your submitted job name
TRACK=SenAT?

# OMP threads per python process in the topo step's multiprocessing pool
OMP_TOPO=4
############################

python "$(dirname "$0")/scripts/write_slurmJobs.py" -t "$TRACK" --omp-topo "$OMP_TOPO" "$@"
# add --clean to activate the deletion lines (written commented out by default)
