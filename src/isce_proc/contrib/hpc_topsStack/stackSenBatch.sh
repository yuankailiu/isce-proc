# Setup to submit stackSentinel batch to Slurm on HPC
# Author: Yuan-Kai Liu, Oliver Stephenson, April 2023
#               originally from stack_sentinel_cmd.sh
#
# First, run `run_isce_stack.py` (https://github.com/earthdef/sar-proc/tree/main/tools)
# Once you have all the run_files, run this shell to generate slurm jobs
# The default here assumes you do ionosphere correction with NESD coreg., other workflows may work though

# Edit below
############################
# SAR track e.g., a087, AT087, SenAT087; as your submitted job name
TRACK=SenAT?

# computing options
CPUS_PER_TASK_TOPO=4  # For each python process in the pool, how many CPUs to use
############################

## Move relevant scripts for running the tops stack processing chain into run_files
MAIN_DIR=$(dirname "$0")
# Table logging the resources for each stage
cp ${MAIN_DIR}/inputs/resources.cfg ./run_files/
# For writing the SLURM script files for each stage
cp ${MAIN_DIR}/scripts/write_slurmJobs.py ./run_files/
# For submitting the SLURM script files
cp ${MAIN_DIR}/scripts/submit_chained_dependencies.sh ./run_files/
# For erasing data during processing
cp ${MAIN_DIR}/scripts/clean_topsStack_files.sh ./run_files/
# For analysing timings after processing
cp ${MAIN_DIR}/scripts/analyse_time_resource.py ./run_files/

cwd=$(pwd)
cd ./run_files


## Write SLURM script files for submitting each stage separately
python write_slurmJobs.py -t $TRACK


# Sed statement below is used to edit the relevant SLURM script files
#################################################################################
# TODO:
#   + reduce the comlicated sed and bash tricks here...

echo 'Editing SLURM script files'
## Edit SLURM files
# Change number of OpenMP threads for topo stage
# We havea python multiprocessing pool of resources, each of which can use OpenMP
# The number of python multiprocessing processes is controlled by num_process4topo
sed -i "s/OMP_NUM_THREADS=\$SLURM_CPUS_PER_TASK/OMP_NUM_THREADS=$CPUS_PER_TASK_TOPO/g" run_01_unpack_topo_reference.job

# Get an email when the final step finishes
last_job_script=$(ls run_*.job | tail -n 1)
sed 's/--mail-type=FAIL/--mail-type=FAIL,END/' -i $last_job_script


## File deletion
# Add deleting scripts to SLURM files (NB need to edit them to turn off the dry run)
# Choose what to delete by passing command line arguments
# NB - when using slurm arrays we move the deletion to one stage later, to avoid one job deleting the files needed by another running job with a different array index
# Replace the line '#_deletion_here' in SLURM statement
# Use the if statement to just do the deletion using the first slurm array, we don't want to repeat this from every array element
# Need to espace '/' for sed
# Calling with 'srun' gives us more informative logs when looking at 'sacct' output
target_runfiles=(             # no. step      comment
'run_*_fullBurst_geo2rdr'     #  09        do not delete if consider burst properties in ionosphere computation: True
'run_*_generate_burst_igram'  #  13
'run_*_merge_burst_igram'     #  14
'run_*_filter_coherence'      #  15
'run_*_unwrap'                #  16
'run_*_mergeBurstsIon'        #  19
'run_*_unwrap_ion'            #  20        do not delete if consider burst properties in ionosphere computation: True
'run_*_filtIon'               #  23        do not delete if consider burst properties in ionosphere computation: True
)
target_runfiles=($(ls $(echo ${target_runfiles[@]})))


tops_stack_opts=(
'--esd --coreg_overlap'
'--geom_reference'
'--coarse_igram'
'--burst_igram'
'--burst_slc'
'--ion_burst_slc'
'--ion_burst_igram'
'--ion_split_igram --coreg_offset'
)
for ((i=0;i<${#target_runfiles[@]};i++)); do
    tops_stack_opt=${tops_stack_opts[i]}
    run_file=${target_runfiles[i]}

    # loop over max of 8000 pairs
    for fext in .job .p1.job .p2.job .p3.job .p4.job .p5.job .p6.job .p7.job .p8.job
    do
        sbatch_file=${run_file}${fext}
        if [ -f "${sbatch_file}" ]; then
            # use double quote to enable variable usage
            sed "s/#_deletion_here/##if [[ \$SLURM_ARRAY_TASK_ID -eq 1 ]]; then srun .\/clean_topsStack_files.sh ${tops_stack_opt}; fi/g" -i ${sbatch_file}
        fi
    done
done

# Reminder to not leave the script on 'dry_run'
echo "Make sure to switch on deleting in the clean_topsStack_files.sh script"

# Write header for file size log
# fmt_fs="%-35s%-12s%-12s%-12s\\n"
fmt_fs="%-35s%-12s%-12s%-12s%-12s\\n"
printf "$fmt_fs" "Step"  "Step number" "Job ID" "Task ID" "Total size" > total_file_sizes.txt

# Go back to process dir
cd $cwd
echo 'Normal finished'
