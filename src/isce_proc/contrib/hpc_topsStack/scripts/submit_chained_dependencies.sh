#!/bin/bash
set -e
# Submit a series of scripts to a SLURM job manager, each depending on the previous one finishing correctly.
#
# Usage: ./submit_chained_dependencies.sh [OPTIONS]
#
# Options:
#   -s <start_num> : Specify a starting numerical step (e.g., -s 1). Used with -e for a range.
#   -e <end_num>   : Specify an ending numerical step (e.g., -e 10). Used with -s for a range.
#                    If only -s is given, it submits from start_num to the end.
#                    If only -e is given, it submits from 1 to end_num.
#   -l <num1> [num2 ...] : Submit a specific list of job numbers sequentially (e.g., -l 24 26 27 28).
#                          Can also be used for a single job (e.g., -l 15).
#                          This option takes precedence over -s and -e.
#
# If no options are provided, all 'run*.job' files in the current directory will be submitted sequentially.
#
# Example usage:
#   ./submit_chained_dependencies.sh                 # Submit all jobs
#   ./submit_chained_dependencies.sh -s 1 -e 16      # Submit jobs from step 1 to 16
#   ./submit_chained_dependencies.sh -s 17           # Submit jobs from step 17 to the end
#   ./submit_chained_dependencies.sh -l 24 26 27 28  # Submit jobs for a list of steps
#   ./submit_chained_dependencies.sh -l 16           # Submit only job for step 16


# TODO - remove the debug queue from Sbatch option when running big jobs
echo 'Submitting topsStack jobs. Make sure that ISCE is loaded or these will fail'

# NOTE - if restarting mid way through the processing, need to deal with the timings file and remove finished steps from the list below

run_dir=$(dirname "$0")
cur_dir=${PWD}
cd "${run_dir}" # Change to the directory where the script resides

# Helper function to extract the primary numerical step ID from a run*.job filename
# Handles formats like 'run1.job', 'run_16_unwrap.p1.job', 'run19_mergeBurstsIon.job'
get_step_num() {
    local filename=$(basename "$1") # Ensure we're working with just the filename part
    # Use regex to find the first sequence of digits after 'run' (optionally followed by '_')
    if [[ "$filename" =~ run_?([0-9]+) ]]; then
        # Strip leading zeros by using arithmetic expansion
        echo $((10#${BASH_REMATCH[1]}))
    else
        echo "0" # Return 0 if no number is found
    fi
}


# List of SLURM job scripts that we want to run
step_sta=0; step_end=0
declare -a job_list_args=() # Array to store job numbers passed with -l

while getopts s:e:l flag
do
    case "${flag}" in
        s) step_sta=${OPTARG};;  # start step (a serial steps)
        e) step_end=${OPTARG};;  #   end step (a serial steps)
        l) # Capture subsequent arguments for -l
           # Shift OPTIND to consume the -l flag, then capture remaining positional args
           shift $((OPTIND-1))
           job_list_args=("$@")
           # Exit getopts loop as -l consumes all remaining args
           break
           ;;
        *)
            echo "Usage: $0 [-s START -e END] | [-l STEP1 [STEP2 ...]]" >&2
            echo "       -s: starting step for a serial submission" >&2
            echo "       -e: ending step for a serial submission" >&2
            echo "       -l: list of specific job numbers to submit sequentially (can be a single job)" >&2
            exit 1
            ;;
    esac
done

# Enable nullglob to ensure that patterns that match no files expand to nothing (empty list)
shopt -s nullglob

declare -a sbatch_files=() # Array to hold the final list of sbatch files

if [ ${#job_list_args[@]} -gt 0 ]; then
    echo "Submitting specified jobs from list: ${job_list_args[@]}"
    # Iterate through all 'run*.job' files in the current directory using direct globbing
    for file in run*.job; do
        file_step_num=$(get_step_num "$file")
        for arg_step_num in "${job_list_args[@]}"; do
            if [ "$file_step_num" -eq "$arg_step_num" ]; then
                sbatch_files+=("$file") # Store relative path
                break # Found a match for this file, move to next file
            fi
        done
    done
    # Ensure the list of files is numerically sorted based on the run number
    IFS=$'\n' sbatch_files=($(sort -V <<<"${sbatch_files[*]}"))
    unset IFS
elif [ "$step_sta" == 0 ] && [ "$step_end" == 0 ]; then
    echo "All steps will be submitted"
    sbatch_files=($(ls run*.job | sort -V)) # Use sort -V for natural sorting
else
    # if [ "$step_exc" != 0 ]; then step_sta=$step_exc; step_end=$step_exc; fi # Removed -x logic
    if [ "$step_sta" == 0 ]; then step_sta=1; fi
    if [ "$step_end" == 0 ]; then step_end=999; fi
    if [ "$step_end" -lt "$step_sta" ]; then step_end=$step_sta; fi
    echo "Starting step: $step_sta"; echo "Ending step  : $step_end";

    # Find all run*.job files and filter by numerical range using the helper function and direct globbing
    for file in run*.job; do
        file_step_num=$(get_step_num "$file")
        if (( file_step_num >= step_sta )) && (( file_step_num <= step_end )); then
            sbatch_files+=("$file") # Store relative path
        fi
    done
    # Numerically sort the collected files
    IFS=$'\n' sbatch_files=($(sort -V <<<"${sbatch_files[*]}"))
    unset IFS
fi

# Disable nullglob after file collection (optional, but good practice)
shopt -u nullglob

num_file=${#sbatch_files[@]}

# Check if any files were found to be submitted
if [ "$num_file" -eq 0 ]; then
    echo "Error: No job files found matching the specified criteria. Exiting."
    exit 1
fi

# Check that we have all the run_* and .job scripts in this directory before submitting
for ((i=0;i<${num_file};i++)); do
    sbatch_file=${sbatch_files[i]}
    run_file=$(basename "${sbatch_file}" | cut -d. -f1) # Get basename for the companion script check

    # check existence
    if [ ! -f "${run_file}" ]; then
        echo "${run_file} does not exist, exiting"
        exit
    fi
    # Original script didn't check for sbatch_file existence here, relying on `ls *.job` or `find`
    # and the new `shopt -s nullglob` ensuring valid files.
done

echo 'File checks passed. Submitting jobs'

### Log files
start=`date +%s`
now=$(date '+%Y-%m-%d %T')
echo "$now"
printf -v date '%(%Y-%m-%d)T' -1 # Store this command in $date for file naming
# Explanation of the printf command: https://stackoverflow.com/questions/30098992/what-does-printf-v-do
# TODO not sure if writing every array element to this log is helpful, probably slows things down quite a lot - consider removing
logfile="cmd_runall_${date}.log"

if [ -f "${logfile}" ] ; then
    rm -f "${logfile}"
fi

printf "####=========================================####\n" > "${logfile}"
printf "####=========================================####\n" >> "${logfile}"
printf "    Submitting all jobs at: %s \n" "$(date)" >> "${logfile}"
printf "####=========================================####\n" >> "${logfile}"
printf "####=========================================####\n\n\n\n" >> "${logfile}"

## Create files specifically for logging timings
# One is in an easy format for quick inspection, the other in unix format for further processing
# TODO add date to timings file - need to pass the name to sbatch
fmt="%-35s%-12s%-12s%-12s%-12s%-12s\n" # Need to modify this in write_sbatch_files.py as well
# printf -v date '%(%Y-%m-%d)T' -1 # Store this command in $date for file naming and start/ending times
printf "# Job submitted at: %s\n" "$now" >> timings.txt
printf "$fmt" "# Stage" "Job ID" "Array ID" "Start" "Finish" "Elapsed" >> timings.txt # Create a file to write all timings to, which is then written to by each stage
# Write a file with unix times for later processing
now_unix=$(date "+%s")
printf "# Job submitted at: %s\n" "$now_unix" >> time_unix.txt
printf "$fmt" "# Stage" "Job ID" "Array ID" "Start (s)" "Finish (s)" "Elapsed (s)" >> time_unix.txt # Create a file to write all timings to, which is then written to by each stage

### SUBMIT JOBS
# Submit the first job
sbatch_file_to_submit=${sbatch_files[0]} # This is now the relative path
run_file=$(basename "${sbatch_file_to_submit}" | cut -d. -f1)

# Pass the logfile as an argument
# Need 'ALL' so we get other environment variables
# Add -q debug to use debug queue (will hit job threshold when using slurm arrays)
ID=$(sbatch --parsable --export=ALL,logfile="${logfile}" "${sbatch_file_to_submit}")
echo "Submitted 1/${num_file} ${sbatch_file_to_submit} - $ID"
# Write a logfile with the ID of each stage
id_logfile="job_id_logfile_${date}.txt"
echo "IDs of Jobs submitted at: $now" >> "${id_logfile}"
fmt_id="%-35s%-12s\\n"
printf "$fmt_id" "Stage" "Job ID" >> "${id_logfile}"
printf "$fmt_id" "${run_file}" "$ID" >> "${id_logfile}"
# echo $ID >> $id_logfile

# Loop over remaining scripts and submit them
last_job_id="$ID" # Initialize with the first job's ID
for ((i=1;i<${num_file};i++)); do
    sbatch_file_to_submit=${sbatch_files[i]}
    run_file=$(basename "${sbatch_file_to_submit}" | cut -d. -f1)

    ID=$(sbatch --parsable --dependency=afterok:"${last_job_id}" --export=ALL,logfile="${logfile}" "${sbatch_file_to_submit}")
    printf "$fmt_id" "${run_file}" "$ID" >> "${id_logfile}"
    echo "Submitted $((i+1))/${num_file} ${sbatch_file_to_submit} - $ID"
    last_job_id="$ID" # Update the dependency ID for the next iteration
done

# Note - if one job fails, all the rest will stay in the queue. Can kill all of your jobs by doing scancel -u <username>

cd "${cur_dir}" # Change back to the original directory
echo 'All jobs submitted. Have a nice day.'

