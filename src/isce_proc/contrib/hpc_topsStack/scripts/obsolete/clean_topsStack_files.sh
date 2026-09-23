#!/bin/bash
############################################################
#                     REAME                                #
# Author: Oliver Stephenson, Yuan-Kai Liu, Jan 2022        #
############################################################

# ==================  DRY RUN  ========================
# false: only print out file list & sizes to log
# true : we actually erase files
delete_files_bool="false"
# =====================================================

############################################################
#                 Help message                             #
############################################################
Help()
{
   # Display Help
   echo "              << HELP PAGE >>                         "
   echo "Delete the major outputs of ISCE2 topsStack processor "
   echo "Need to run script from within run_files/ folder      "
   echo "            << Use with care >>                       "
   echo
   echo "Syntax: clean_topsStack_files.sh [-h] LIST_OF_DELETIONS "
   echo
   echo "Options:"
   echo "   -h                : Print this Help."
   echo "   LIST_OF_DELETIONS : append a list of files to delete, see below table:"
   echo "    ------------------------------------------------------------------------------------------------------------------------ "
   echo "      < Deletions >      < Relevant files >       < c# | k# >            < Regex patterns >                                  "
   echo "    ----------------  ---------------------------  ---------  -------------------------------------------------------------- "
   echo "    --geom_reference   geometry reference bursts    1 | 12     geom_reference/IW.*/.*\.rdr                                   "
   echo "    --coarse_igram     coarse ifgrams               7 | 13     coarse_interferograms/20.*_20.*/overlap/IW.*/int_.*\.int      "
   echo "    --burst_igram      burst ifgrams               13 | 14     interferograms/20.*_20.*/IW.*/fine_.*\.int                    "
   echo "    --burst_slc        burst SLCs                  10 | 15     coreg_secondarys/20.*/IW.*/burst_[0-9][0-9]\.slc              "
   echo "    --merged_slc       merged SLCs (if -V=False)   12 | *      merged/SLC/20.*/.*\.slc.full                                  "
   echo "    --ion_burst_slc    ion burst SLCs              17 | 18     coreg_secondarys/20.*/IW.*/burst_[0-9][0-9]_(lower|upper)\.slc"
   echo "    --ion_burst_igram  ion burst ifgrams           18 | 19     ion/20.*_20.*/(lower|upper)/interferograms/IW.*/fine_.*\.int  "
   echo "    --ion_split_igram  ion split-band ifgrams      19 | 22     ion/20.*_20.*/(lower|upper)/merged/.*\.(int|cor|unw|conncomp) "
   echo "    --coreg_offset     azimuth and range offsets    9 | 22     coreg_secondarys/20.*/IW.*/.*\.off                            "
   echo "    --coreg_overlap    overlap slcs and offsets    5,6| 28     coreg_secondarys/20.*/overlap/IW.*/.*\.(slc|off)              "
   echo "    --esd              ESD files                    7 | 28     ESD/20.*_20.*/IW.*/.*\.(int|bin|cor|off)                      "
   echo "    --ion_burst_ramp   ion burst-level burst ramp  27 | 28     ion_burst_ramp_dates/20.*/IW.*/burst.*\.(float)               "
   echo "    ---------------------      --------------------------------------------------------------------------------------------- "
   echo "      c# : created at           The step at which those files are created                                                    "
   echo "      k# : kill after           The step after completing which is safe to delete those files without obstructing processing "
   echo "      *  : --merged_slc         Not used in the topsStack 'interferogram' workflow, can delete merged SLCs if you have them  "
   echo "      These steps number are counted in the stackSentinel.py 'interferogram' workflow                                        "
   echo "    -----------------------------      ------------------------------------------------------------------------------------- "
   echo "    Quick actions:    --all              delete above all files                                                              "
   echo "                      --dry_run          do not delete files, just print out file list and sizes to log, takes long time!!   "
   echo "                      --delete           actually delete files, default is dry_run                                           "
   echo "    ------------------------------------------------------------------------------------------------------------------------ "
   echo
   echo "Recommend usage:"
   echo "  1. Delete the coarse interferograms                                  "
   echo "    clean_topsStack_files.sh --coarse_igram                            "
   echo "  2. Delete these types of files                                       "
   echo "    clean_topsStack_files.sh --coarse_igram --burst_slc --burst_igram  "
   echo "  3. Delete all relevant files                                         "
   echo "    clean_topsStack_files.sh --all                                     "
   echo "  4. Add specific deletions to the end of particular sbatch job to avoid hitting disk quotas during processing."
   echo
   echo " Actual deleting things now?  '${delete_files_bool}'"
   echo
}

################################################################################
################################################################################
#                     Main program                                             #
################################################################################
################################################################################
# Get Help
while getopts ":h" option; do
    case $option in
        h) # display Help
            Help
            exit;;
    esac
done


step_start=`date +%s`
current_dir=$(pwd)
cd .. # Assuming that we'll be running this from 'run_files'

# Store this command in $date for file naming
printf -v date '%(%Y-%m-%d)T' -1 # about printf -v: https://stackoverflow.com/questions/30098992/what-does-printf-v-do

# Write all logs to the same file each time you run this script (TODO: write that to that specific log file of that stage job run)
logfile=$current_dir/"delete_files_${date}.log"

# Exit if we don't have any command line arguments
if [ $# -eq 0 ]
    then
        echo "No arguments supplied, exiting with HELP"           | tee -a $logfile
        Help
        exit 1
fi

run_date=`date`
echo "##########################################################" | tee -a $logfile
echo "  Calling clean_topsStack_files.sh at: $run_date"           | tee -a $logfile
echo "  Current working directory: $current_dir"                  | tee -a $logfile
echo "  Arguments: $@"                                            | tee -a $logfile

################### Regex patterns of paths of files to delete ######################
## Note:
##     + need to escape .
##     + .* matches any number of characters
##     + entry in the 'case' setup below to read from command line for each of these
## Comment info:
##     Target files [created by step | can be delete after step]
#####################################################################################

## geom reference burst files
#   created at : run_01_unpack_topo_reference
#   kill after : run_12_merge_reference_secondary
geom_reference_path='\./geom_reference/IW.*/.*\.rdr'

## coarse interferograms
#   created at : run_07_pairs_misreg
#   kill after : run_13_generate_burst_igram
coarse_path='\./coarse_interferograms/20.*_20.*/overlap/IW.*/int_.*\.int'

## burst interferograms
#   created at : run_13_generate_burst_igram
#   kill after : run_14_merge_burst_igram
burst_igram_path='\./interferograms/20.*_20.*/IW.*/fine_.*\.int'

## regular burst slcs
#   created at : run_10_fullBurst_resample
#   kill after : run_15_filter_coherence
# we need them to calculate the coherence (via .vrt files in merged/SLC/)
burst_slc_path='\./coreg_secondarys/20.*/IW.*/burst_[0-9][0-9]\.slc'

## regular ifg merged slcs
#   created at : run_12_merge_reference_secondary_slc
#   kill after : any step; Only used in 'SLC' or 'offset' workflow. For 'interferogram' workflow, you can delete merged SLCs anytime you like
# so no calculation is based on that except you want to do offset tracking / damage proxy map.
merged_slc_path='\./merged/SLC/20.*/.*\.slc.full'

## upper and lower burst SLCs
#   created at : run_17_subband_and_resamp
#   kill after : run_18_generateIgram_ion
ion_burst_slc_path='\./coreg_secondarys/20.*/IW.*/burst_[0-9][0-9]_(lower|upper)\.slc'

## ion burst interferograms
#   created at : run_18_generateIgram_ion
#   kill after : run_19_merge_BurstsIon
ion_burst_igram_path='\./ion/20.*_20.*/(lower|upper)/interferograms/IW.*/fine_.*\.int'

## split band merged interferograms
#   created at : run_19_merge_BurstsIon
#   kill after : run_22_computeIon
# looks like we don't have .cor files in ion/20*_20*/upper/merged/*.cor
ion_split_igram_path='\./ion/20.*_20.*/(lower|upper)/merged/.*\.(int|cor|unw|conncomp)'

## coregistered secondarys azimuth/range offsets
#   created at : run_09_fullBurst_geo2rdr
#   kill after : run_22_computeIon
coreg_offset_path='\./coreg_secondarys/20.*/IW.*/.*\.off'

## coregistered secondarys overlap files
#   created at : run_05_overlap_geo2rdr, run_06_overlap_resample
#   kill after : run_08_timeseries_misreg; run_28_mergeBurstRampIon if you need ion burst-ramp correction
coreg_overlap_path='\./coreg_secondarys/20.*/overlap/IW.*/.*\.(slc|off)'

## esd files
#   created at : run_07_pairs_misreg
#   kill after : run_08_timeseries_misreg; run_28_mergeBurstRampIon if you need ion burst-ramp correction
esd_path='\./ESD/20.*_20.*/IW.*/.*\.(int|bin|cor|off)'


## burst-level ion burst ramp estimates
#   created at : run_27_burstRampIon
#   kill after : run_28_mergeBurstRampIon
ion_burst_ramp_path='\./ion_burst_ramp_dates/20.*/IW.*/burst.*\.(float)'

## TODO:
#  if the file patterns is wrong, it leads to bad results for total size of deleted files
#  alternatively, we can end up calling du on the whole directory to get the right size
#####################################################################################


# Parser for command-line options. Idiomatic parameter and option handling in sh:
#      https://superuser.com/questions/186272/check-if-any-of-the-parameters-to-a-bash-script-match-a-string/186279
del_paths=()
echo "  Deleting options:" | tee -a $logfile
while test $# -gt 0
do
    case "$1" in
        --geom_reference)  del_paths+=("$geom_reference_path");  echo "  + geometry reference burst files"       | tee -a $logfile  ;;
        --coarse_igram)    del_paths+=("$coarse_path");          echo "  + coarse interferograms"                | tee -a $logfile  ;;
        --burst_igram)     del_paths+=("$burst_igram_path");     echo "  + burst interferograms"                 | tee -a $logfile  ;;
        --burst_slc)       del_paths+=("$burst_slc_path");       echo "  + burst slcs"                           | tee -a $logfile  ;;
        --merged_slc)      del_paths+=("$merged_slc_path");      echo "  + merged slcs"                          | tee -a $logfile  ;;
        --ion_burst_slc)   del_paths+=("$ion_burst_slc_path");   echo "  + ionosphere burst slcs"                | tee -a $logfile  ;;
        --ion_burst_igram) del_paths+=("$ion_burst_igram_path"); echo "  + ionosphere burst interferograms"      | tee -a $logfile  ;;
        --ion_split_igram) del_paths+=("$ion_split_igram_path"); echo "  + ionosphere split-band interferograms" | tee -a $logfile  ;;
        --coreg_offset)    del_paths+=("$coreg_offset_path");    echo "  + azimuth and range offset files"       | tee -a $logfile  ;;
        --coreg_overlap)   del_paths+=("$coreg_overlap_path");   echo "  + overlap slcs and offset files"        | tee -a $logfile  ;;
        --esd)             del_paths+=("$esd_path");             echo "  + ESD files"                            | tee -a $logfile  ;;
        --ion_burst_ramp)  del_paths+=("$ion_burst_ramp_path");  echo "  + ion burst-level burst ramp estimates" | tee -a $logfile  ;;
        --all)             del_paths+=("$geom_reference_path");
                           del_paths+=("$coarse_path");
                           del_paths+=("$burst_igram_path");
                           del_paths+=("$burst_slc_path");
                           del_paths+=("$merged_slc_path");
                           del_paths+=("$ion_burst_slc_path");
                           del_paths+=("$ion_burst_igram_path");
                           del_paths+=("$ion_split_igram_path");
                           del_paths+=("$coreg_offset_path");
                           del_paths+=("$coreg_overlap_path");
                           del_paths+=("$esd_path");
                           del_paths+=("$ion_burst_ramp_path");  echo "  + All deletion options activated"       | tee -a $logfile ;;

        --help)            Help; exit 0 ;; # Print help and exit
        --dry_run)         delete_files_bool="false"; echo " ** You specify : --dry_run" | tee -a $logfile ;;
        --delete)          delete_files_bool="true";  echo " ** You specify : --delete"  | tee -a $logfile ;;
        *) echo "Argument $1 not understood" ;; # Keep running even if one argument is wrong - TODO might want to terminate the whole run if we make a mistake here
    esac
    shift
done

echo "----------------------------------------------------------" | tee -a $logfile
if [ "$delete_files_bool" = "true" ];
then
    echo ' [Deleting / Dry run] : Actual Deleting Files!'         | tee -a $logfile
else
    echo ' [Deleting / Dry run] : Neh, just a Dry Run...'         | tee -a $logfile
fi
echo "##########################################################" | tee -a $logfile

# Alias find command using regular expressions
find_regex='find ./ -regextype posix-extended -regex'

# Loop over del_paths and delete the files for each path
size_arr=()
size_byte_arr=()
for path in "${del_paths[@]}"
do
    # Enclose "$path" to prevent wildcards being expanded
    # echo "$path"
    echo "## Deleting files matching regex: $path" | tee -a $logfile
    echo "File list:" >> $logfile
    $find_regex $path >> $logfile
    # eval ls $path >> $logfile

    # size=$($find_regex $path | xargs du -ch | tail -1 | cut -f 1) # xargs divides arguments into batches, so you only get totals by batch
    # size_bytes=$($find_regex $path | xargs du -c | tail -1 | cut -f 1) # Get bytes for later summing
    # TODO - stat fails if we run this on files that we've already deleted
    echo 'Calculating sizes of deleted files at: ' $(date) | tee -a $logfile # This can be time consuming for large numbers of files
    size_bytes=$($find_regex $path | xargs stat -c '%s' |awk '{total=total+$1}END{total = total; print total}') # Get size in bytes
    echo 'Done calculating sizes of deleted files at: ' $(date) | tee -a $logfile

    size=$(echo "$size_bytes / (1024*1024*1024*1024)" | bc -l ) # Scale into TB; TODO there must be a neater way than this
    # Do deletions
    if [ "$delete_files_bool" = "true" ];
    then
        echo 'Deleting at: ' $(date) | tee -a $logfile
        # TODO deleting large numbers of files like this is slow - explore other options
        # Need 'find' and 'xargs' to deal with very large numbers of files (ls/du/rm will fail when over ~300k files)
        $find_regex $path | xargs rm -f
        echo 'Finished deleting at: ' $(date) | tee -a $logfile
    fi

    if [ "$delete_files_bool" = "true" ];
    then
        # TODO could automatically scale into TB vs GB depending on the size
        printf "*** Size deleted: %.3f TB \n\n" $size | tee -a $logfile
    else
        printf "*** Size that would be deleted: %.3f TB \n\n" $size | tee -a $logfile
    fi
    size_arr+=("$size") # Append the size of deleted files to an array
    size_bytes_arr+=($size_bytes) # Append as number
done

echo "------------------------------------------------------------"   | tee -a $logfile
# Get size of remaining files
echo 'Calculating sizes of all files at: ' $(date)                    | tee -a $logfile
size_remain=$(eval du -ch --max-depth=1 . | tail -1 | cut -f 1) # Total amount of data we have left,
echo 'Done calculating sizes of all files at: ' $(date)               | tee -a $logfile
echo "------------------------------------------------------------"   | tee -a $logfile

# Get total size of deleted files in bytes
# From here: https://stackoverflow.com/questions/13635293/how-can-i-find-the-sum-of-the-elements-of-an-array-in-bash
sum=$(IFS=+; echo "$((${size_bytes_arr[*]}))")

# Calculate time for all erasing (make sure this is at the end of the code)
# If deletion is slow, we might want to start it as a separate job that doesn't hold up subsequent jobs
step_end=`date +%s`
step_time=$( echo "$step_end - $step_start" | bc -l )
Elapsed="$(($step_time / 3600))hrs $((($step_time / 60) % 60))min $(($step_time % 60))sec"

# Output stats
echo "##################################" | tee -a $logfile
echo "##################################" | tee -a $logfile
echo "Total time: $Elapsed"               | tee -a $logfile
# Convert into GB
if [ "$delete_files_bool" = "true" ];
then
    printf "Total deleted: %.3f TB \n" $(echo "$sum / (1024*1024*1024*1024)" | bc -l)               | tee -a $logfile
else
    printf "Total that would be deleted: %.3f TB \n" $(echo "$sum / (1024*1024*1024*1024)" | bc -l) | tee -a $logfile
fi
echo "Size remaining: $size_remain"       | tee -a $logfile # We repeat this for every deletion - lets move it outside

# Add a reminder if we're doing a dry run
if [ "$delete_files_bool" = "false" ];
then
    echo 'Reminder: dry run, we did not deleting files' | tee -a $logfile
fi
echo "##################################" | tee -a $logfile
echo "##################################" | tee -a $logfile
printf "\n\n\n"                           | tee -a $logfile

# Back to run_files
cd $current_dir
