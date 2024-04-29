# Python script to write sbatch files for tops stack on Caltech's HPC
# Author: Yuan-Kai Liu, Oliver Stephenson, April 2023

# This script is executed under run_files/

import argparse
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd

# Caltech Resnick HPC PTA group name
GROUPNAME = 'simonsgroup'

# email of the user
mail_user = os.environ.get("USER")

# Check the nodes/CPUs available in your system. ≤ this many CPUs per node (https://www.hpc.caltech.edu/resources)
CPUS_PER_NODE_LIM = 56

# max array size of slurm (to break large jobs into multiple sbatch files)
# scontrol show config | grep -E 'MaxArraySize|MaxJobCount'
SLURM_MAX_ARRAY_SIZE = 1000

# limit the num of tasks in a job array run at once to avoid I/O traffic
max_task = 200


def cmdLineParse():
    '''
    Command line parsers
    '''
    description = 'Generates SLURM job scripts for each stage of topsStack for Caltech HPC'

    EXAMPLE = f"""Examples:
        ## {__file__} TRACK_NO
    """
    epilog = EXAMPLE
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawTextHelpFormatter,  epilog=epilog)

    parser.add_argument('-t', '--track', dest='track_no', type=str, required=True,
                        help = 'Track number, for naming the job (e.g. T115a)')
    parser.add_argument('-r', '--rsc', dest='rsc_file', type=str, default='resources.cfg',
                        help = 'resources configuration table for all topsStack stages')
    parser.add_argument('-j', '--job', dest='job_template', type=str, default='../inputs/slurm.job',
                        help = 'slurm script template')

    if len(sys.argv) <= 1:
        print('')
        parser.print_help()
        sys.exit(1)
    else:
        print('')
        return parser


#########################################################################################

def check_resources(rscDf):
    """Checking your resource config table
    """
    for index, row in rscDf.iterrows():
        step_name       = row['Step']
        nodes           = row['Nodes']
        ntasks          = row['Ntasks']
        ncpus_per_task  = row['Ncpus_per_task']

        # Check resource limits
        cpus_per_node   = ntasks * ncpus_per_task / nodes
        if cpus_per_node > CPUS_PER_NODE_LIM:
            raise Exception(f'Do not exceed {CPUS_PER_NODE_LIM} cpus per node')
        if step_name == 'unwrap':
            if cpus_per_node > CPUS_PER_NODE_LIM/4:
                raise Exception('Do not exceed {CPUS_PER_NODE_LIM} cpus per node for unwrapping stage due to memory issues')
                # Ollie: I think this isn't a problem when we're using arrays
                #       this will vary a lot depending on the size of the region that's being processed
                #       Should experiment with this depending on your situation
                #       I think unwrapping stage can only use 1 CPU, but needs a lot of memory
    print('>> Resource table checking passed')
    return True


def timestr2sec(time_str):
    # SBATCH --time supported time format:
    #       "minutes", "minutes:seconds", "hours:minutes:seconds",
    #       "days-hours", "days-hours:minutes" and "days-hours:minutes:seconds"

    d, h, m ,s = 0, 0, 0, 0

    if len(time_str.split(':')) == 3:
        if len(time_str.split(':')[0].split('-')) == 2:
            fmt = '%d-%H:%M:%S'
            time_str = time_str.replace('-',':')
            d, h, m, s = time_str.split(':')
        else:
            fmt = '%H:%M:%S'
            h, m, s = time_str.split(':')

    elif len(time_str.split(':')) == 2:
        if len(time_str.split(':')[0].split('-')) == 2:
            fmt = '%d-%H:%M'
            time_str = time_str.replace('-',':')
            d, h, m = time_str.split(':')
        else:
            fmt = '%M:%S'
            m, s = time_str.split(':')

    elif len(time_str.split('-')) == 2:
        fmt = '%d-%H'
        time_str = time_str.replace('-',':')
        d, h = time_str.split(':')

    else:
        fmt = '%M'
        m = time_str

    dt = 86400*float(d) + 3600*float(h) + 60*float(m) + float(s)
    return dt, fmt


def write_job_scripts(inps):
    print(f'>> Writing SLURM job scripts for {inps.track_no}')

    # check the rsc file
    if check_resources(inps.rscDf):
        pass

    # Read/write stackSentienl run_files:
    runfiles = sorted([x for x in Path.cwd().glob('run_*') if not '.' in str(x)])
    step_scripts = []
    for run in runfiles:
        step_scripts.append(run.stem)

    # Iterate over the run files, write an sbatch file for each one
    for index, step_script in enumerate(step_scripts):
        # a table of steps
        step_num        = step_script[:6]
        step_name       = step_script[7:]
        time            = inps.rscDf[inps.rscDf['Step']==step_name]['Time'].item()
        nodes           = inps.rscDf[inps.rscDf['Step']==step_name]['Nodes'].item()
        ntasks          = inps.rscDf[inps.rscDf['Step']==step_name]['Ntasks'].item()
        ncpus_per_task  = inps.rscDf[inps.rscDf['Step']==step_name]['Ncpus_per_task'].item()
        mem             = inps.rscDf[inps.rscDf['Step']==step_name]['Mem_per_cpu'].item()
        gres            = inps.rscDf[inps.rscDf['Step']==step_name]['Gres'].item()

        # assign to a HPC partition w/ or w/o gpus
        # The default partition for The Resnick HPCC will change from “any” (CentOS 7) to “expansion” (RHEL 9) on Tuesday, March 26th.
        if int(gres) > 0: partition = 'gpu'
        else: partition = 'expansion'

        # Get the number of commands in the script
        cmd_num = len(open(step_script).readlines())

        # enough walltime to compute and document the file size?
        if timestr2sec(time)[0] >= 1800.0:
            # if walltime more than 30 min, task no. 2 will check filesize
            check_disk = 2
        else:
            # no checking
            check_disk = 0

        # split large sbatch file into multiple parts if needed
        num_sbatch = np.ceil(cmd_num / SLURM_MAX_ARRAY_SIZE).astype(int)
        for i in range(num_sbatch):
            # use ROWINDEX, instead of SLURM_ARRAY_TASK_ID, to select line of interest
            # link: https://stackoverflow.com/questions/67908698/submitting-slurm-array-job-with-a-limit-above-maxarraysize
            task_id1   = min(SLURM_MAX_ARRAY_SIZE, cmd_num - i * SLURM_MAX_ARRAY_SIZE)  # ending task index of the current job
            row_id0    = i * SLURM_MAX_ARRAY_SIZE                                       # starting row index of the current job
            suffix     = '' if num_sbatch == 1 else f'.p{i+1}'
            log_name   = f'slurm-{step_script}-%A_%a{suffix}.out'
            slurm_name = f'{step_script}{suffix}.job'

            context = {
                "groupname"         :   GROUPNAME,
                "time"              :   time,
                "nodes"             :   nodes,
                "ntasks"            :   ntasks,
                "ncpus_per_task"    :   ncpus_per_task,
                "log_name"          :   log_name,
                "track"             :   inps.track_no,
                "step_name"         :   step_name,
                "step_num"          :   step_num,
                "step_script"       :   step_script,
                "step_index"        :   index+1,
                "mail_user"         :   mail_user,
                "row_id0"           :   row_id0,
                "task_id1"          :   task_id1,
                "max_task"          :   max_task,
                "gres"              :   gres,
                "partition"         :   partition,
                "mem"               :   mem,
                "check_disk_list"   :   check_disk,
                # "ntasks_per_node" :   row['Ntasks_per_node'],
            }

            # Put variables from context dic into the slurm script template
            print(' '+slurm_name)
            with open(slurm_name, 'w') as outf:
                outf.write(inps.template.format(**context))

    print(f'create job scripts for {inps.track_no}.')


def write_end_cmd(cmd_script='run_atTheEnd.sh', log_dir='log_files'):
    """Create a final bash cmd for logfiles & ime documenting
    """
    with open(cmd_script, 'w') as outf:
        outf.write(f'#!/bin/bash\n')
        outf.write(f'# Commands after topsStack processing. Run this after all the jobs are finished\n\n')
        outf.write(f'mkdir -p {log_dir}\n')
        outf.write(f'mv *.out *.txt *.log {log_dir}/ \n')
        outf.write(f'reportseff ./{log_dir} --no-color > {log_dir}/reportseff_all.txt\n')
        outf.write(f'python analyse_time_resource.py\n')
    print(f'create {cmd_script} to run by yourself after all jobs on HPC finished.')


#################################################################
def main(iargs=None):
    # parser
    parser = cmdLineParse()
    inps = parser.parse_args(args=iargs)

    # read input resource config and slurm template
    inps.rscDf = pd.read_table(inps.rsc_file, header=0, delim_whitespace=True)
    inps.template = open(inps.job_template, 'r').read()

    # write *.job scripts
    write_job_scripts(inps)

    # write end cmmands for post-documenting
    write_end_cmd()

    # done
    run_dir = Path().absolute().name
    print(f'Now, get into {run_dir}/ and run `submit_chained_dependencies.sh` for jobs submission!')


#################################################################
if __name__ == '__main__':
    main(sys.argv[1:])
