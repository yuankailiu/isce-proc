#!/usr/bin/env python3

import os
import re
import argparse
import logging
from datetime import datetime

script_name = os.path.basename(__file__)

HELP_MESSAGE = f"""
                    << HELP PAGE >>
    Delete the major outputs of ISCE2 topsStack processor.
    Need to run script from within run_files/ folder.

                   ! Use with care !

    Options:
        LIST_OF_DELETIONS : append a list of files to delete, see below table:
        ____________________________________________________________________________________________________________________
          < Deletions >      < Relevant files >     < c# | k# >          < Regex patterns >
        _________________  _________________________ _________ _____________________________________________________________
        --coreg_overlap    overlap slcs and offsets  5,6| 8    \./coreg_secondarys/20.*/overlap/IW.*/.*\.(slc|off)
        --esd              ESD files                  7 | 8    \./ESD/20.*_20.*/IW.*/.*\.(int|bin|cor|off)
        --geom_reference   geometry reference bursts  1 | 12   \./geom_reference/IW.*/.*\.rdr
        --coarse_igram     coarse ifgrams             7 | 13   \./coarse_interferograms/20.*_20.*/overlap/IW.*/int_.*\.int
        --burst_igram      burst ifgrams             13 | 14   \./interferograms/20.*_20.*/IW.*/fine_.*\.int
        --burst_slc        burst SLCs                10 | 15   \./coreg_secondarys/20.*/IW.*/burst_[0-9][0-9]\.slc
        --ion_burst_slc    ion burst SLCs            17 | 18   \./coreg_secondarys/20.*/IW.*/burst_[0-9][0-9]_(lower|upper)\.slc
        --ion_burst_igram  ion burst ifgrams         18 | 19   \./ion/20.*_20.*/(lower|upper)/interferograms/IW.*/fine_.*\.int
        --ion_split_igram  ion split-band ifgrams    19 | 22   \./ion/20.*_20.*/(lower|upper)/merged/.*\.(int|cor|unw|conncomp)
        --coreg_offset     azimuth and range offsets  9 | 22   \./coreg_secondarys/20.*/IW.*/.*\.off
        --safe             safe zip files             0 | 1    \./SLC/.*\.zip
        ____________________________________________________________________________________________________________________
              c# ; created at   - The step at which those files are created
              k# ; kill after   - The step after completing which is safe to delete those files without obstructing processing
              These steps number are counted in the stackSentinel.py 'interferogram' workflow

    Recommend usage:
      1. Delete the coarse interferograms
        {script_name} coarse_igram

      2. Delete these types of files
        {script_name} coarse_igram burst_slc burst_igram

      3. Delete all relevant files
        {script_name} all

      4. Add specific deletions to the end of particular sbatch job to avoid hitting disk quotas during processing.
    """


def help_to_parserDict(HELP_MESSAGE):
    # Split help message into lines
    lines = HELP_MESSAGE.split("\n")

    # Initialize dictionary
    delDict = {}

    # Regex pattern to match deletion lines
    pattern = r'^\s*--(?P<deletion>\S+)\s+(?P<relevant_files>.+)\s+(?P<created_at>[\d\s,]+)\s*\|\s*(?P<kill_after>[\d\s,]+)\s+(?P<regex_pattern>.+)$'

    # Iterate over lines
    for line in lines:
        match = re.match(pattern, line)
        if match:
            deletion       = match.group('deletion')
            relevant_files = match.group('relevant_files').strip()
            created_at     = match.group('created_at').strip()
            kill_after     = match.group('kill_after').strip()
            regex_pattern  = match.group('regex_pattern').strip()
            delDict[deletion] = {}
            delDict[deletion]['relevant_files'] = relevant_files
            delDict[deletion]['created_at']     = created_at
            delDict[deletion]['kill_after']     = kill_after
            delDict[deletion]['regex']          = regex_pattern
    return delDict


def delete_path(path, kill, calc):
    find_command = f'find ./*/ -regextype posix-extended -regex "{path}"'
    logging.info(find_command)
    files = os.popen(find_command).read()
    logging.info('files matching regex:')
    output = os.poopen(f'du -sch {files}').read()
    logging.info(output)

    if calc:
        # Calculate total size of files deleted
        deleted_size = int(os.popen(f'{output} | grep "total"').read().split()[0])
        size_bytes = os.popen(f'numfmt --from=iec --suffix=B --format="%10.2f" {deleted_size}').read().split('B')[0]

    if kill:
        try:
            # Use xargs to efficiently delete files
            logging.info(f'deleting...')
            #os.system(f'echo {files} | xargs rm -f')
            os.system(f'echo {files} | rsync -av --files-from=- --delete-before --remove-source-files . /dev/null')
        except Exception as e:
            logging.error(f'Error deleting files matching regex {path}: {e}')

    return size_bytes if calc else 0


def delete_paths(paths, kill, calc):
    total_deleted_size = 0

    # loop over each path pattern
    for path in paths:
        total_deleted_size += delete_path(path, kill, calc)

    # convert unit
    output = os.popen(f'numfmt --to=iec --suffix=B --format="%10.2f" {total_deleted_size}').read()

    if kill:
        logging.info(f"Total size deleted: {output}")
    else:
        logging.info(f"Total size that would be deleted: {output}")

    return total_deleted_size if calc else None



if __name__ == "__main__":
    # create input parsers
    parser = argparse.ArgumentParser(description='Delete stackSentinel files.', formatter_class=argparse.RawTextHelpFormatter, epilog=HELP_MESSAGE)
    parser.add_argument('deletions'  , metavar='DELETIONS', type=str, nargs='+'    , help='List of deletions')
    #parser.add_argument('--calc'     , dest='calc'        , action='store_true'    , help='Calculate delete file sizes')
    parser.add_argument('--kill'     , dest='kill'        , action='store_true'    , help='Actual delete the files')
    parser.add_argument('--log-mode' , dest='log_mode'    , type=str, default='w+' , help='Open the log file in this mode. (default: %(default)s)\n')
    args = parser.parse_args()
    args.calc = True

    # get datetime
    current_date = datetime.now().strftime('%Y-%m-%d')

    # log saving
    log_file = f'delete_files_{current_date}.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s: %(message)s',
        handlers=[
                logging.FileHandler(log_file, mode=args.log_mode),  # Output to a log file
                logging.StreamHandler()  # Output to the console
                ]
        )

    print(f'Actual_deletion: {args.kill}    Calculate_size: {args.calc}')
    print('-'*60)

    # create regex dictionary from help
    delDict = help_to_parserDict(HELP_MESSAGE)

    # get deletions
    deletion_paths = []
    for deletion in delDict.keys():
        if (deletion in args.deletions) or ('all' in args.deletions):
            deletion_paths += [ delDict[deletion]['regex'] ]
            print(deletion, ' '*10,  delDict[deletion]['relevant_files'], )
    print('-'*60)


    # run delete
    total_deleted_size = delete_paths(deletion_paths, args.kill, args.calc)
