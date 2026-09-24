#!/usr/bin/env python

################################################
# Post-select regular insar pairs to run
# Does not support select ionospheric pairs
################################################

import glob
import shutil
from pathlib import Path

overwrite_bak     = False
num_connections   = 5
connection_bridge = 10

run_dir  = Path('./run_files')
pre_dir  = Path(run_dir/'preselect')
safefile = 'SAFE_files.txt'

# get all the dates from the SAFE_FILE
dateList = []
with open(safefile, 'r') as f:
    lines = f.read().splitlines()
    for line in lines:
        date = line.split('/')[-1].split('_')[6][:8]
        if date not in dateList:
            dateList.append(date)

dateList = sorted(dateList)
print(dateList)
print(len(dateList))

num_date = len(dateList)


# selecting nearest pairs based on dateList and num_connections
pairList = []
for i in range(num_date):
    for j in range(i+1, i+1+connection_bridge):
        if j < num_date:
            if not ((j >= i+1+num_connections) and (j < i+connection_bridge)):
                pairList.append(f'{dateList[i]}_{dateList[j]}')

print('selecting pairs with {} nearest neighbor connections & {} far-bridging connections: {} \
      '.format(num_connections, connection_bridge, len(pairList)))


# create folder to store the pre-select original runfiles
pre_dir.mkdir(parents=True, exist_ok=True)

# grab the interferogram-related runfiles
files = sorted(glob.glob(str(run_dir/'run_*')))
check_list = {'generate_burst_igram':   'config_generate_igram_',
              'merge_burst_igram'   :   'config_merge_igram_',
              'filter_coherence'    :   'config_igram_filt_coh_',
              'unwrap'              :   'config_igram_unw_'
              }
ckeck_files = []
for runfile in files:
    for x in check_list.keys():
        if runfile.endswith(x):
            ckeck_files.append(runfile)


# backup runfiles
for runfile in ckeck_files:
    bak_file = pre_dir / Path(runfile).name
    if not overwrite_bak:
        if bak_file.exists():
            print(f'orignal runfile exists in the backup folder {pre_dir}, skip copy and overwriting it')
        else:
            shutil.copyfile(runfile, bak_file)
            print(f'backup the original runfile to: {bak_file}')
    else:
        shutil.copyfile(runfile, bak_file)
        print(f'backup the original runfile to: {bak_file}')


# read/write new content
for (key, cfg), runfile in zip(check_list.items(), ckeck_files):
    # the original backup runfile
    bak_file = pre_dir / Path(runfile).name
    # create new run file and add selected pairs
    pairs = []
    with open(runfile, 'w') as outf:
        with open(bak_file, 'r') as inf:
            lines = inf.read().splitlines()
            for line in lines:
                config = line.split()[-1]
                pair = Path(config).name.split(cfg)[-1]
                if pair in pairList:
                    pairs.append(pair)
                    outf.write(f'{line}\n')
    print(f'written selected pairs in {runfile} with {len(pairs)} lines.')

print('Finished.')
