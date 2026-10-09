# Python script to write sbatch files for tops stack on Caltech's HPC
# Author: Yuan-Kai Liu, Oliver Stephenson, April 2023
#
# Run from the stack directory (holding run_files/) or from run_files/, after stackSentinel.py:
#     python <isce-proc>/contrib/hpc_topsStack/scripts/write_slurmJobs.py -t a076
# It copies the helper scripts into run_files/, writes one sbatch file per run_file
# (split into .pN parts above the array limit), a disk-usage job, and run_atTheEnd.sh.
# Then submit with:  cd run_files; bash submit_chained_dependencies.sh

import argparse, glob
import os
import shutil
import subprocess
import sys
from pathlib import Path
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import clean_topsStack

# Caltech Resnick HPC PTA group name
GROUPNAME = 'simonsgroup'

# email of the user
mail_user = os.environ.get("USER")

######################## --------------------  ########################
########################  YOUR HPC CAPABILITY  ########################
######################## --------------------  ########################

# Check the nodes/CPUs available in your system. ≤ this many CPUs per node (https://www.hpc.caltech.edu/resources)
CPUS_PER_NODE_LIM = 56

# maximum slurm array size (to break large jobs into multiple sbatch files)
# due to Caltech HPC limit
#   MaxArraySize            = 1001
#   MaxJobCount             = 100000
# check with: scontrol show config | grep -E 'MaxArraySize|MaxJobCount'
SLURM_MAX_ARRAY_SIZE = 1000

# The number of tasks in a job array run at once is the `batch` column of resources.cfg
# (limits I/O traffic; 200 is fine on Resnick, 32 is very conservative)

######################## --------------------  ########################
########################  YOUR HPC CAPABILITY  ########################
######################## --------------------  ########################

# copied into run_files/ so the stack is self-contained
HELPERS = ['submit_chained_dependencies.sh', 'clean_topsStack.py', 'check_topsStack.py']

DISK_JOB = """#!/bin/bash
# Record the stack size after a step. Submitted by submit_chained_dependencies.sh with
#   --dependency=afterany:<step job>,singleton --export=ALL,STEP=<run_file>
# so no step waits for it, and only one of these runs at a time.
#SBATCH -A {groupname}
#SBATCH -J disk_usage_{track}
#SBATCH --time=4:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --partition=expansion
{exclude_line}#SBATCH --output=slurm-disk_usage-%j.out

du -h --max-depth=1 ..
total=$(du -sh .. | cut -f1)
printf "%-35s%-12s%-12s%-12s%-12s\\n" "${{STEP#run_??_}}" "${{STEP:0:6}}" "$SLURM_JOB_ID" "-" "$total" >> total_file_sizes.txt
"""


GATE_JOB = """#!/bin/bash
# Gate between two steps, submitted by submit_chained_dependencies.sh with
#   --dependency=afterany:<all parts of the step> --export=ALL,STEP=<run_file>[,GATE_FINAL=1]
# Checks the step's outputs row by row (check_topsStack.py). Bad rows: submits the reruns and a
# follow-up gate (up to {retries} times) and moves the next step onto that gate; still bad after
# that: exits non-zero (the next step never starts) and mails what failed and what to run.
# After the last step (GATE_FINAL=1) it mails a summary when everything is good.
# Short time limit on purpose: small jobs start quickly through backfill.
#SBATCH -A {groupname}
#SBATCH -J gate_{track}
#SBATCH --time=00:30:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --partition=expansion
{exclude_line}#SBATCH --mail-type=NONE
#SBATCH --output=slurm-gate-%j.out

export TOPSSTACK_MAIL={mail} TOPSSTACK_TRACK={track} TOPSSTACK_TEMPLATE={template} TOPSSTACK_BIN={topsstack}
{python} check_topsStack.py "$(echo "$STEP" | cut -d_ -f2)" --gate {retries} -n 2
rc=$?
if [ $rc -ne 0 ] && [ ! -f ".gate_mailed_$SLURM_JOB_ID" ]; then     # the gate itself failed (no mail sent)
    tail -30 "slurm-gate-$SLURM_JOB_ID.out" | mail -s "[topsstack {track}] gate for $STEP failed (rc=$rc), chain stopped" {mail}
fi
rm -f ".gate_mailed_$SLURM_JOB_ID"
exit $rc
"""


def cmdLineParse():
    '''
    Command line parsers
    '''
    description = 'Generates SLURM job scripts for each stage of topsStack for Caltech HPC'

    EXAMPLE = f"""Examples:
        {Path(__file__).name} -t a076
        {Path(__file__).name} -t a076 --clean      # activate the file deletion lines
    """
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawTextHelpFormatter, epilog=EXAMPLE)

    parser.add_argument('-t', '--track', dest='track_no', type=str, required=True,
                        help = 'Track name, for naming the jobs (e.g. a076)')
    parser.add_argument('-r', '--rsc', dest='rsc_file', type=str, default=None,
                        help = 'resources configuration table (default: run_files/resources.cfg, else ../inputs/resources.cfg)')
    parser.add_argument('-j', '--job', dest='job_template', type=str, default=None,
                        help = 'slurm script template (default: inputs/slurm.job next to this script)')
    parser.add_argument('--omp-topo', dest='omp_topo', type=int, default=4,
                        help = 'OMP_NUM_THREADS for unpack_topo_reference (default: %(default)s); '
                               'its python pool uses Ncpus_per_task / this many processes')
    parser.add_argument('--clean', dest='clean', action='store_true',
                        help = 'write the deletion lines active instead of commented out')
    parser.add_argument('--gate', dest='gate', type=int, default=None, metavar='N',
                        help = 'write gate.job: between steps, check outputs and rerun bad rows up to N times')
    parser.add_argument('--gate-steps', dest='gate_steps', type=str, default='all', metavar='"1 5 7"',
                        help = 'with --gate: steps followed by a gate, or all (default: %(default)s); '
                               'the last submitted step always gets one -> gate_steps.txt')
    parser.add_argument('--disk-steps', dest='disk_steps', type=str, default='all', metavar='"1 10 12"',
                        help = 'steps followed by a disk_usage job, or all (default: %(default)s) -> disk_steps.txt')
    parser.add_argument('--gpu-type', dest='gpu_type', type=str, default='v100',
                        help = 'GPU type for steps with Gres > 0 in resources.cfg (default: %(default)s; '
                               'Slurm here requires --gres=gpu:<type>:<count>)')
    parser.add_argument('--no-gpu', dest='no_gpu', action='store_true',
                        help = 'request no GPU for any step (isce.useGPU = no)')
    parser.add_argument('--template', dest='track_template', type=str, default=None,
                        help = 'track template; run_atTheEnd.sh then calls `topsstack.py report` with it')
    parser.add_argument('--fuse-merge', dest='fuse_merge', action='store_true',
                        help='no job for generate_burst_igram: each merge_burst_igram task generates its pair, merges it and '
                             'deletes the burst interferograms (peak disk: running tasks only, not all pairs)')
    parser.add_argument('--fuse-ion', dest='fuse_ion', action='store_true',
                        help='no job for generateIgram_ion: each mergeBurstsIon task generates its pair (both sub-bands), '
                             'merges it and deletes the sub-band burst interferograms')
    parser.add_argument('--fuse-mem', dest='fuse_mem', type=str, default='8G',
                        help='memory per CPU of the fused task (default: %(default)s; merge max RSS 3.3 GB on d083/a076)')
    parser.add_argument('--exclude', dest='exclude', type=str, default='',
                        help='nodes to avoid, e.g. hpc-21-14,hpc-21-15 (tasks there failed at start: exit 0:53, no log)')
    parser.add_argument('--account', dest='account', type=str, default=GROUPNAME,
                        help = 'Slurm account (default: %(default)s)')
    parser.add_argument('--mail', dest='mail', type=str, default=f'{mail_user}@caltech.edu',
                        help = 'mail address for FAIL/END notices (default: %(default)s)')
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


def row_parts(rows, first_alone=False):
    """[(first row index, number of rows)] per job part: a new part where the program changes, and
    at most SLURM_MAX_ARRAY_SIZE rows each. first_alone: row 1 is a part of its own (it creates a
    file that the other rows would otherwise race to create)."""
    progs = [r.split()[0] if r.split() else '' for r in rows]
    parts, start = [], 0
    for k in range(1, len(rows) + 1):
        if (k == len(rows) or progs[k] != progs[start] or k - start == SLURM_MAX_ARRAY_SIZE
                or (first_alone and k == 1)):
            parts.append((start, k - start))
            start = k
    return parts


def deletion_lines(clean):
    """Map each clean_topsStack target to the step after its kill-after step.

    The deletion runs in array task 1 of that next step, i.e. only after every task
    of the kill-after step has exited OK (afterok chain). Slurm COMPLETED does not
    prove the outputs exist, so the lines are commented out unless --clean.
    """
    steps, zip_vrt, virtual, ion = clean_topsStack.stack_info(str(Path.cwd().parent))
    nums = sorted(steps.values())
    attach = {}
    for target, (_, kill, _) in clean_topsStack.rules(zip_vrt, virtual, ion).items():
        if kill in steps and steps[kill] < nums[-1]:
            nxt = next(n for n in nums if n > steps[kill])
            attach.setdefault(nxt, []).append(target)
    prefix = '' if clean else '# '
    return {n: f'{prefix}if [[ $SLURM_ARRAY_TASK_ID -eq 1 ]]; then srun python clean_topsStack.py {" ".join(t)} --delete; fi'
            for n, t in attach.items()}


def add_times(a, b):
    """Sum of two Slurm times ([D-]HH:MM:SS, MM:SS) as H:MM:SS."""
    def sec(t):
        d, _, t = t.rpartition('-')
        p = [int(x) for x in t.split(':')]
        p = [0] * (3 - len(p)) + p
        return (int(d) if d else 0) * 86400 + p[0] * 3600 + p[1] * 60 + p[2]
    s = sec(a) + sec(b)
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


FUSED = """cmd=$(sed "${{ROWINDEX}}q;d" {merge})
# fused (write_slurmJobs.py --fuse-merge): generate this pair's burst interferograms, merge them, delete them
pair=$(echo "$cmd" | grep -oE '[0-9]{{8}}_[0-9]{{8}}' | tail -1)
gen=$(grep -m1 -E "config_generate_igram_${{pair}}$" {gen})
idir=$(awk -F' : ' '/^inp_reference/{{print $2}}' "${{cmd##* }}")
echo "Running: ${{gen}}" | tee -a $logfile
srun $gen 2>&1
rc=$?
if [ $rc -eq 0 ]; then
    echo "Running: ${{cmd}}" | tee -a $logfile
    srun $cmd 2>&1
    rc=$?
fi
if [ $rc -eq 0 ] && [ -n "$idir" ] && [ -d "$idir" ]; then
    find "$idir" -name 'fine_*.int' -type f -delete         # merged: keep only .xml/.vrt ('cleaned' in status)
    echo "deleted burst interferograms of $pair" | tee -a $logfile
fi"""


FUSED_ION = """cmd=$(sed "${{ROWINDEX}}q;d" {merge})
# fused (write_slurmJobs.py --fuse-ion): generate this pair's sub-band burst interferograms, merge, delete them
pair=$(echo "$cmd" | grep -oE '[0-9]{{8}}-[0-9]{{8}}' | tail -1 | tr - _)
gen=$(grep -m1 -E "config_generateIgram_ion_${{pair}}$" {gen})
cfg="${{cmd##* }}"
echo "Running: ${{gen}}" | tee -a $logfile
srun $gen 2>&1
rc=$?
if [ $rc -eq 0 ]; then
    echo "Running: ${{cmd}}" | tee -a $logfile
    srun $cmd 2>&1
    rc=$?
fi
if [ $rc -eq 0 ]; then
    for idir in $(awk -F' : ' '/^dirname/{{print $2}}' "$cfg"); do      # ion/<pair>/{{lower,upper}}/interferograms
        [ -d "$idir" ] && find "$idir" -name 'fine_*.int' -type f -delete
    done
    echo "deleted sub-band burst interferograms of $pair" | tee -a $logfile
fi"""


def fuse_generate(text, merge_script, gen_script, block=None):
    """Replace the single-command block of the merge job by generate + merge + delete for the row's pair."""
    import re
    pat = re.compile(r'^cmd=\$\(sed "\$\{ROWINDEX\}q;d" ' + re.escape(merge_script) + r'\)\n.*?^rc=\$\?$', re.M | re.S)
    if not pat.search(text):
        raise ValueError('job template changed: cannot fuse generate_burst_igram into merge_burst_igram')
    return pat.sub(lambda m: (block or FUSED).format(merge=merge_script, gen=gen_script), text, count=1)


def write_job_scripts(inps):
    print(f'>> Writing SLURM job scripts for {inps.track_no}')

    # check the rsc file
    if check_resources(inps.rscDf):
        pass

    # Read/write stackSentienl run_files:
    runfiles = sorted([x for x in Path.cwd().glob('run_*') if not '.' in x.name])
    step_scripts = []
    for run in runfiles:
        step_scripts.append(run.stem)
    deletions = deletion_lines(inps.clean)

    # Iterate over the run files, write an sbatch file for each one
    gen_script = next((x for x in step_scripts if x[7:] == 'generate_burst_igram'), None)
    gen_ion_script = next((x for x in step_scripts if x[7:] == 'generateIgram_ion'), None)
    # fused steps: the generate step has no job and the fused (merge) job also generates, so a deletion
    # attached to the generate step moves to the merge step, and one attached to the merge step (its kill-after
    # step is the generate step) moves to the step after the merge
    nums = [int(x[4:6]) for x in step_scripts]
    for on, gen, mrg in ((inps.fuse_merge, 'generate_burst_igram', 'merge_burst_igram'),
                         (inps.fuse_ion, 'generateIgram_ion', 'mergeBurstsIon')):
        g = next((n for n, x in zip(nums, step_scripts) if x[7:] == gen), None)
        m = next((n for n, x in zip(nums, step_scripts) if x[7:] == mrg), None)
        if on and g and m and m != nums[-1]:
            after = nums[nums.index(m) + 1]
            moved = {after: deletions.pop(m, None), m: deletions.pop(g, None)}
            for n, line in moved.items():
                if line:
                    deletions[n] = line + ('\n' + deletions[n] if n in deletions else '')
    for index, step_script in enumerate(step_scripts):
        # a table of steps
        step_num        = step_script[:6]
        step_name       = step_script[7:]
        if inps.fuse_ion and step_name == 'generateIgram_ion':
            for old_job in glob.glob(f'{step_script}*.job'):
                os.remove(old_job)
            print(f' {step_script}: fused into mergeBurstsIon (no job)')
            continue
        if inps.fuse_merge and step_name == 'generate_burst_igram':
            for old_job in glob.glob(f'{step_script}*.job'):
                os.remove(old_job)                       # its rows run inside merge_burst_igram
            print(f' {step_script}: fused into merge_burst_igram (no job)')
            continue
        row             = inps.rscDf[inps.rscDf['Step']==step_name]
        time            = row['Time'].item()
        if inps.fuse_merge and step_name == 'merge_burst_igram' and gen_script:
            gen_time = inps.rscDf[inps.rscDf['Step']=='generate_burst_igram']['Time'].item()
            time = add_times(time, gen_time)
        if inps.fuse_ion and step_name == 'mergeBurstsIon' and gen_ion_script:
            g = inps.rscDf[inps.rscDf['Step']=='generateIgram_ion']
            time = add_times(time, g['Time'].item())
        nodes           = row['Nodes'].item()
        ntasks          = row['Ntasks'].item()
        ncpus_per_task  = row['Ncpus_per_task'].item()
        mem             = row['Mem_per_cpu'].item()
        if inps.fuse_merge and step_name == 'merge_burst_igram' and gen_script:
            mem = inps.fuse_mem
        if inps.fuse_ion and step_name == 'mergeBurstsIon' and gen_ion_script:
            mem = '4G'                                           # a076 max RSS: generateIgram_ion 1.6 GB, mergeBurstsIon 3.2 GB
            ncpus_per_task = inps.rscDf[inps.rscDf['Step']=='generateIgram_ion']['Ncpus_per_task'].item()  # >4 CPUs for its I/O
        gres            = row['Gres'].item()
        max_task        = row['batch'].item()
        if inps.fuse_ion and step_name == 'mergeBurstsIon' and gen_ion_script:
            max_task = min(int(max_task), 100)                   # bounds the sub-band burst interferograms on disk

        # assign to a HPC partition w/ or w/o gpus
        # The default partition for The Resnick HPCC will change from “any” (CentOS 7) to “expansion” (RHEL 9) on Tuesday, March 26th.
        gres = 0 if inps.no_gpu else int(gres)
        if gres > 0: partition = 'gpu'
        else: partition = 'expansion'
        gres_line = (f'#SBATCH --gres=gpu:{inps.gpu_type}:{gres}                  # GPUs (geo2rdr steps)\n'
                     if gres > 0 else '')

        # split the rows into parts: at program changes (e.g. run_22: computeIon.py rows, then the
        # mergeSwathIon.py rows that need them; parts run one after another) and at the array limit.
        # filtIon: row 1 alone first, it creates merged/geom_reference/waterBody_ionlk.rdr (rows started
        # at the same time failed in 4-8 s reading a half-written file, d083 2026-10-04)
        parts = row_parts(open(step_script).read().splitlines(), first_alone=step_name == 'filtIon')
        num_sbatch = len(parts)
        gate_list = None if inps.gate_steps.strip() == 'all' else set(inps.gate_steps.replace(',', ' ').split())
        gated = lambda num, last: inps.gate is not None and (last or gate_list is None or
                                                             str(int(str(num).split('_')[-1])) in gate_list or step_name in gate_list)
        for i, (row_id0, task_id1) in enumerate(parts):
            # use ROWINDEX, instead of SLURM_ARRAY_TASK_ID, to select line of interest
            # link: https://stackoverflow.com/questions/67908698/submitting-slurm-array-job-with-a-limit-above-maxarraysize
            suffix     = '' if num_sbatch == 1 else f'.p{i+1}'
            log_name   = f'slurm-{step_script}-%A_%a{suffix}.out'
            slurm_name = f'{step_script}{suffix}.job'
            is_last    = (index == len(step_scripts) - 1) and (i == num_sbatch - 1)

            context = {
                "exclude_line"      :   f'#SBATCH --exclude={inps.exclude}\n' if inps.exclude else '',
                "groupname"         :   inps.account,
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
                "mail"              :   inps.mail,
                # a gate after this step sends the (detailed) mails; otherwise Slurm FAIL (+ END at the end),
                # so a stop at a step without a gate is not silent
                "mail_type"         :   'NONE' if gated(step_num, is_last) else ('FAIL,END' if is_last else 'FAIL'),
                "row_id0"           :   row_id0,
                "task_id1"          :   task_id1,
                "max_task"          :   max_task,
                "gres_line"         :   gres_line,
                "partition"         :   partition,
                "mem"               :   mem,
                # topo runs a python pool of Ncpus_per_task/omp_topo processes, each with omp_topo threads
                "omp_threads"       :   inps.omp_topo if step_name == 'unpack_topo_reference' else '$SLURM_CPUS_PER_TASK',
                # delete only from the first part of a multi-part step
                "deletion"          :   deletions.get(int(step_num[4:]), '') if i == 0 else '',
            }

            # Put variables from context dic into the slurm script template
            print(' '+slurm_name)
            text = inps.template.format(**context)
            if inps.fuse_merge and step_name == 'merge_burst_igram' and gen_script:
                text = fuse_generate(text, step_script, gen_script)
            if inps.fuse_ion and step_name == 'mergeBurstsIon' and gen_ion_script:
                text = fuse_generate(text, step_script, gen_ion_script, FUSED_ION)
            with open(slurm_name, 'w') as outf:
                outf.write(text)

    with open('disk_usage.job', 'w') as outf:
        outf.write(DISK_JOB.format(groupname=inps.account, track=inps.track_no,
                                    exclude_line=f'#SBATCH --exclude={inps.exclude}\n' if inps.exclude else ''))
    if inps.gate is not None:
        with open('gate.job', 'w') as outf:
            outf.write(GATE_JOB.format(groupname=inps.account, track=inps.track_no, mail=inps.mail,
                                       exclude_line=f'#SBATCH --exclude={inps.exclude}\n' if inps.exclude else '',
                                       retries=inps.gate, python=sys.executable, template=inps.track_template or '',
                                       topsstack=SCRIPT_DIR.parent / 'topsstack.py'))
        gs = inps.gate_steps.strip()
        if inps.fuse_merge and gs != 'all' and 'generate_burst_igram' in gs.split():
            gs += ' merge_burst_igram'                   # the fused job carries generate_burst_igram's rows
        with open('gate_steps.txt', 'w') as outf:
            outf.write(gs + '\n')
        print(f' gate.job (reruns per step: {inps.gate}; after steps: {inps.gate_steps} + the last)')
    elif os.path.exists('gate.job'):
        os.remove('gate.job')                            # gate off: plain afterok chain
    with open('total_file_sizes.txt', 'w') as outf:
        outf.write(f'{"Step":35s}{"Step number":12s}{"Job ID":12s}{"Task ID":12s}{"Total size":12s}\n')
    with open('disk_steps.txt', 'w') as outf:
        outf.write(inps.disk_steps.strip() + '\n')
    print(f' disk_usage.job (after steps: {inps.disk_steps})')
    for n, line in sorted(deletions.items()):
        print(f'   deletion in run_{n:02d}: {line}')
    print(f'create job scripts for {inps.track_no}.')


def write_end_cmd(template=None, cmd_script='run_atTheEnd.sh'):
    """Create a final bash cmd for resource/timing reporting (moves no files: status/clean/report read them here)."""
    topsstack = SCRIPT_DIR.parent / 'topsstack.py'
    with open(cmd_script, 'w') as outf:
        outf.write('#!/bin/bash\n')
        outf.write('# Commands after topsStack processing. Run this after all the jobs are finished\n\n')
        outf.write('command -v reportseff >/dev/null && reportseff . --no-color > reportseff_all.txt\n')
        if template:
            outf.write(f'{sys.executable} {topsstack} report {template}\n')
    print(f'create {cmd_script} to run by yourself after all jobs on HPC finished.')


#################################################################
def main(iargs=None):
    # parser
    inps = cmdLineParse().parse_args(args=iargs)

    # work inside run_files/
    if Path('run_files').is_dir():
        os.chdir('run_files')
    if Path.cwd().name != 'run_files':
        sys.exit('run from the stack directory or its run_files/')

    # copy the helper scripts (always, so they match this version) and the default
    # resources.cfg (only if missing: it is edited per track) next to the run_files
    inputs = SCRIPT_DIR.parent / 'inputs'
    for h in HELPERS:
        shutil.copy(SCRIPT_DIR / h, h)
    if not Path('resources.cfg').exists():
        shutil.copy(inputs / 'resources.cfg', 'resources.cfg')
    rev = subprocess.run(['git', '-C', str(SCRIPT_DIR), 'describe', '--always', '--dirty', '--all', '--long'],
                         capture_output=True, text=True).stdout.strip() or 'unknown'
    with open('isce_proc_version.txt', 'w') as f:
        f.write(f'{SCRIPT_DIR}\n{rev}\n')

    # read input resource config and slurm template
    inps.rsc_file = inps.rsc_file or 'resources.cfg'
    inps.job_template = inps.job_template or str(inputs / 'slurm.job')   # must match this script's fields
    inps.rscDf = pd.read_table(inps.rsc_file, header=0, sep=r'\s+')
    inps.template = open(inps.job_template, 'r').read()

    # write *.job scripts
    write_job_scripts(inps)

    # write end cmmands for post-documenting
    write_end_cmd(inps.track_template)

    # done
    print('Now run `bash submit_chained_dependencies.sh` here for jobs submission!')


#################################################################
if __name__ == '__main__':
    main(sys.argv[1:])
