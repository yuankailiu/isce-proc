#!/bin/bash
set -e
# Submit a series of scripts to a SLURM job manager, each depending on the previous one finishing correctly
# Flags: -x submit a step;   -s starting step;    -e ending step

# TODO - remove the debug queue from Sbatch option when running big jobs
echo 'Submitting topsStack jobs. Make sure that ISCE is loaded or these will fail'

# NOTE - if restarting mid way through the processing, need to deal with the timings file and remove finished steps from the list below

run_dir=$(dirname "$0")
cur_dir=${PWD}
cd ${run_dir}

# List of SLURM job scripts that we want to run
step_exc=0; step_sta=0; step_end=0
while getopts x:s:e: flag
do
    case "${flag}" in
        x) step_exc=${OPTARG};;  # step to execute (a single step)
        s) step_sta=${OPTARG};;  # start step (a serial steps)
        e) step_end=${OPTARG};;  #   end step (a serial steps)
    esac
done
if [ "$step_exe" == 0 ] && [ "$step_sta" == 0 ] && [ "$step_end" == 0 ]; then
    echo "All steps will be submitted"
    sbatch_files=(`ls *.job`)
else
    if [ "$step_exc" != 0 ]; then step_sta=$step_exc; step_end=$step_exc; fi
    if [ "$step_sta" == 0 ]; then step_sta=1; fi
    if [ "$step_end" == 0 ]; then step_end=999; fi
    if [ "$step_end" -lt "$step_sta" ]; then step_end=$step_sta; fi
    sbatch_files=(`find run*.job -type f | awk 'match($0,/[0-9]+/,a)&&a[0]>='$step_sta | awk 'match($0,/[0-9]+/,a)&&a[0]<='$step_end`)
    echo "Starting step: $step_sta"; echo "Ending step  : $step_end";
fi
num_file=${#sbatch_files[@]}

# Check that we have all the run_* and .job scripts in this directory before submitting
for ((i=0;i<${num_file};i++)); do
    # get run_file name
    # link: https://stackoverflow.com/questions/2664740/extract-file-basename-without-path-and-extension-in-bash
    sbatch_file=${sbatch_files[i]}
    run_file=$(basename ${sbatch_file} | cut -d. -f1)

    # check existence
    if [ ! -f "${run_file}" ]; then
        echo "${run_file} does not exist, exiting"
        exit
    fi
    if [ ! -f "${sbatch_file}" ]; then
        echo "${sbatch_file} does not exist, exiting"
        exit
    fi
done

echo 'File checks passed. Submitting jobs'

# echo 'Sourcing ISCE'
# $(makran_proj)


### Log files
start=`date +%s`
now=$(date '+(%Y-%m-%d) %T')
echo $now
printf -v date '%(%Y-%m-%d)T' -1 # Store this command in $date for file naming
# Explanation of the printf command: https://stackoverflow.com/questions/30098992/what-does-printf-v-do
# TODO not sure if writing every array element to this log is helpful, probably slows things down quite a lot - consider removing
logfile="cmd_runall_${date}.log"

if [ -f "${logfile}" ] ; then
    rm -f "${logfile}"
fi

printf "####=========================================####\n" > ${logfile}
printf "####=========================================####\n" >> ${logfile}
printf "    Submitting all jobs at: `date` \n" >> ${logfile}
printf "####=========================================####\n" >> ${logfile}
printf "####=========================================####\n\n\n\n" >> ${logfile}

## Create files specifically for logging timings
# One is in an easy format for quick inspection, the other in unix format for further processing
# TODO add date to timings file - need to pass the name to sbatch
fmt="%-35s%-12s%-12s%-12s%-12s%-12s\n" # Need to modify this in write_sbatch_files.py as well
# printf -v date '%(%Y-%m-%d)T' -1 # Store this command in $date for file naming and start/ending times
printf "# Job submitted at: $now\n" >> timings.txt
printf "$fmt" "# Stage" "Job ID" "Array ID" "Start" "Finish" "Elapsed" >> timings.txt # Create a file to write all timings to, which is then written to by each stage
# Write a file with unix times for later processing
now_unix=$(date "+%s")
printf "# Job submitted at: $now_unix\n" >> time_unix.txt
printf "$fmt" "# Stage" "Job ID" "Array ID" "Start (s)" "Finish (s)" "Elapsed (s)" >> time_unix.txt # Create a file to write all timings to, which is then written to by each stage

### SUBMIT JOBS
# Submit the first job
sbatch_file=${sbatch_files[0]}
run_file=$(basename ${sbatch_file} | cut -d. -f1)
# Pass the logfile as an argument
# Need 'ALL' so we get other environment variables
# Add -q debug to use debug queue (will hit job threshold when using slurm arrays)
ID=$(sbatch --parsable --export=ALL,logfile=${logfile} "${sbatch_file}")
echo "Submitted 1/${num_file} ${sbatch_file} - $ID"
# Write a logfile with the ID of each stage
id_logfile="job_id_logfile_${date}.txt"
echo "IDs of Jobs submitted at: $now" >> $id_logfile
fmt_id="%-35s%-12s\\n"
printf "$fmt_id" "Stage" "Job ID" >> $id_logfile
printf "$fmt_id" "${run_file}" "$ID" >> $id_logfile
# echo $ID >> $id_logfile

# Loop over remaining scripts and submit them
for ((i=1;i<${num_file};i++)); do
    sbatch_file=${sbatch_files[i]}
    run_file=$(basename ${sbatch_file} | cut -d. -f1)

    ID=$(sbatch --parsable --dependency=afterok:${ID} --export=ALL,logfile=${logfile} ${sbatch_file})
    printf "$fmt_id" "${run_file}" "$ID" >> $id_logfile
    echo "Submitted $((i+1))/${num_file} ${sbatch_file} - $ID"
done

# Note - if one job fails, all the rest will stay in the queue. Can kill all of your jobs by doing scancel -u <username>

cd ${cur_dir}
echo 'All jobs submitted. Have a nice day.'
