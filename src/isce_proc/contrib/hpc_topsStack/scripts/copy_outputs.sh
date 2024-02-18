#!/bin/sh
##########################################
# Copy the essential outputs for MintPy
##########################################

nproc=4

track=SenAT?  # edit this for your track number

target_dir=/net/marmot.gps.caltech.edu/mnt/tank/nobak/ykliu/aqaba/topsStack/$track/hpc_isce_stack


##############################
#### Ask for confirmation ####
##############################

echo "Going to copy to the target path:"
echo " ::  $target_dir"
read -p "Are you sure [Y/y] ? " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    exit 1
fi

if [[ -d $target_dir ]]
then
    echo "This directory already exists!!"
    read -p "Are you sure to overwrite content [Y/y] ? " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]
    then
        exit 1
    fi
fi

mkdir -p ${target_dir}


#################################
####### Data for copying ########
#################################


# stack common metadata file
rsync -R reference/IW*.xml ${target_dir}/

# baselines folder
msrsync -P -p ${nproc} --stat -r "-R" baselines ${target_dir}/

# geometry folder
msrsync -P -p ${nproc} --stat -r "-R --exclude '*.full.*'" merged/geom_reference  ${target_dir}/

# interferograms stack folder
mkdir -p ${target_dir}/merged/  # make a placeholder for interferograms
msrsync -P -p ${nproc} --stat merged/interferograms ${target_dir}/merged/

# ionosphere stack (dates) folder
rsync    -avR                         ion/**/ion_cal               ${target_dir}/
msrsync -P -p ${nproc} --stat -r "-R" ion_dates                    ${target_dir}/
msrsync -P -p ${nproc} --stat -r "-R" ion_azshift_dates            ${target_dir}/
msrsync -P -p ${nproc} --stat -r "-R" ion_burst_ramp_merged_dates  ${target_dir}/


# offsets stack folder [optional; add by yourself]


#################################
######### Other copying #########
#################################

# run_files (rsync -avR run_files ${target_dir}/ --exclude 'run_files/log_files')
msrsync -P -p ${nproc} --stat run_files ${target_dir}/

# picture folder
rsync -avR pic ${target_dir}/

# documentation & log files
rsync *.txt *.log *.md ${target_dir}/

echo "Normal complete of copying~~"
