#%%
#!/usr/bin/env python3
########################
# Author: Yuan-Kai Liu
# Date:   Sep, 2023
########################
# Create/copy configs for generating new pairs, will also create new run_files with .aug extension
# Note:
#   + after this script, you need to manually rename/remove '.aug' for the run_files (backup the original ones)
#   + then, need to run stackSenBatch.sh to regenerate the *.job slurm script for submission
# TODO: improve the above manual work.

import os, sys, glob
from pathlib import Path
import shutil

RUN_DIR = './run_files'

#=============================================
def main(steps, pairs):
    # print desired pairs
    print('Your list of pairs to reproduce:')
    print(pairs,'\n')

    # get the run files info
    run_files = []
    for step in steps:
        ll = glob.glob(str(Path(RUN_DIR)/f'run_{step}*'))
        for x in ll:
            if not '.' in x:
                run_files.append(x)


    # get relevant config file paths
    configs = []
    for i, run_file in enumerate(run_files):
        with open(run_file, 'r') as f:
            line = f.readlines()[0]
            configs.append(line.split()[-1])


    # loop over these step configs
    for i, config_src in enumerate(configs):
        if Path(config_src).is_file():
            config_dir  = str(Path(config_src).parent)  # config file directory
            fname_ref   = str(Path(config_src).name)    # reference config file name
            string_list = fname_ref.split('_')          # split filename into list of string
            cfg_base    = ''                            # the base pattern of filename (excluding date1_date2)
            for j, string in enumerate(string_list):
                # the base pattern part of the filename
                if not string[0].isdigit():
                    cfg_base += string+'_'
                # the digit part (date1_date2) of the filename
                else:
                    if '-' in string:  # Cunren uses date1-date2 rather than date1_date2
                        date12_conn = '-'
                        d1, d2 = string.split('-')
                    else:              # otherwise, default isce2 naming
                        date12_conn = '_'
                        if j == len(string_list)-1:
                            # now this code can only handle configs/steps related with pairing interferograms
                            print(f'WARNING: {run_files[i]} {config_src} is not a pairing-related step (date1_date2); expecting errors')
                            sys.exit(1)
                        d1 = string_list[j]
                        d2 = string_list[j+1]
                        break

            # loop over your desired pairs
            for pair in pairs:
                date1, date2 = pair.split('_')
                fname_dst = cfg_base+date1+date12_conn+date2
                config_dst = str(Path(config_dir)/fname_dst)

                # if the config exists, skip copying
                if not Path(config_dst).is_file():
                    continue

                # copy the source config for your desired pair
                shutil.copyfile(config_src, config_dst)

                # read/replace the date12 string in the new config
                with open(config_dst, 'r') as f:
                    data = f.read()
                    data = data.replace(d1, date1)
                    data = data.replace(d2, date2)
                    print(f'create new config {fname_dst}; replace {d1} with {date1}')
                with open(config_dst, 'w') as f:
                    f.write(data)

            # create augmented run_files
            run_aug = run_files[i]+'.aug'
            with open(run_aug, 'w') as outf:
                print(f'write to new augmented run_file: {run_aug}')
                with open(run_files[i], 'r') as inf:
                    line = inf.readlines()[0]
                    cmd_base = line.split(cfg_base)[0]
                    for pair in pairs:
                        date1, date2 = pair.split('_')
                        outf.write(cmd_base+cfg_base+date1+date12_conn+date2+'\n')


if __name__ == '__main__':

    steps = [13, 14, 15, 16]

    #pairs = ['20150223_20150530',
    #         '20150307_20150611',
    #         '20150412_20150623',
    #         '20150506_20150705']

    pairs = ['20141201_20150530',
            '20141225_20150623',
            '20150130_20150717',
            '20150412_20151009',
            '20150223_20150822',
            '20150106_20150705',
            '20150307_20150903',
            '20150506_20151021',
            '20150211_20150729',
            '20141213_20150611']

    main(steps, pairs)

