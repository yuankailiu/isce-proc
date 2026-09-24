#!/bin/sh
##########################################
# Copy the essential outputs for MintPy
#
# Options:
#   --yes : Automatically confirms all prompts, bypassing interactive questions.
##########################################

nproc=4
track=a018  # edit this for your track number
target_dir=/net/marmot.gps.caltech.edu/mnt/tank/nobak/ykliu/chile/$track/hpc_topsStack

# Initialize auto_confirm flag
auto_confirm=false

# Parse command line arguments for --yes
for arg in "$@"; do
    case "$arg" in
        --yes)
            auto_confirm=true
            shift # Consume the --yes argument
            ;;
        *)
            # Handle other arguments if any, or ignore
            ;;
    esac
done

# Function to handle confirmation prompts
# Usage: confirm "Prompt message"
# Returns 0 for yes, 1 for no (and exits if no or not auto-confirmed)
confirm() {
    local prompt_msg="$1"
    if [ "$auto_confirm" = true ]; then
        echo "Auto-confirm enabled. $prompt_msg Auto-confirmed."
        return 0 # Auto-confirm acts as 'yes'
    else
        echo "$prompt_msg"
        read -p "Are you sure [Y/y] ? " -n 1 -r
        echo # Newline after prompt input
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Operation cancelled by user."
            exit 1
        fi
    fi
}

##############################
#### Ask for confirmation ####
##############################

# Initial confirmation for target path
confirm "Going to copy to the target path: $target_dir"

# Confirmation for existing directory
if [ -d "$target_dir" ]; then
    confirm "This directory already exists! Are you sure to overwrite content?"
fi

# Create directory if it doesn't exist (or after confirmation for overwrite)
mkdir -p "${target_dir}"



#################################
####### Data for copying ########
#################################


# stack common metadata file
rsync -R reference/IW*.xml ${target_dir}/

# baselines folder
msrsync3 -P -p ${nproc} --stat -r "-R" baselines ${target_dir}/

# geometry folder
msrsync3 -P -p ${nproc} --stat -r "-R --exclude '*.full.*'" merged/geom_reference  ${target_dir}/

# interferograms stack folder
mkdir -p ${target_dir}/merged/  # make a placeholder for interferograms
msrsync3 -P -p ${nproc} --stat merged/interferograms ${target_dir}/merged/

# ionosphere stack (dates) folder
rsync    -avR                         ion/**/ion_cal               ${target_dir}/
msrsync3 -P -p ${nproc} --stat -r "-R" ion_dates                    ${target_dir}/
msrsync3 -P -p ${nproc} --stat -r "-R" ion_azshift_dates            ${target_dir}/
msrsync3 -P -p ${nproc} --stat -r "-R" ion_burst_ramp_merged_dates  ${target_dir}/


# offsets stack folder [optional; add by yourself]


#################################
######### Other copying #########
#################################
cp SLC/*.log SLC/*.txt SLC/*.csv SLC/*.png SLC/*.pdf .
cp *.log *.txt *.csv pic; mv *.png *.pdf pic

rsync -av --exclude='*.zip' ../data ${target_dir}/

rsync -avR config ${target_dir}/

# run_files (rsync -avR run_files ${target_dir}/ --exclude 'run_files/log_files')
msrsync3 -P -p ${nproc} --stat run_files ${target_dir}/

# picture folder
rsync -avR pic ${target_dir}/

# documentation & log files
rsync *.txt *.log *.md ${target_dir}/

# input and codes
rsync -avR inputs scripts *.sh *.py ${target_dir}/

echo "Normal complete of copying~~"

