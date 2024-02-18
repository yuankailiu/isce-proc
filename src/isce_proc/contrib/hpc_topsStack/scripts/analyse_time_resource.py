#!/usr/bin/env python
############################################################
#                     REAME                                #
# Author: Oliver Stephenson, Yuan-Kai Liu, Jan 2022        #
############################################################
# + Python script to analyse ISCE2 topsStack run times, based on output timings files
# + Addtional function to guage your job’s resource used with sacct
# + Codes are written for Caltech HPC

import os
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from pytz import timezone as ptimezone
import matplotlib.pyplot as plt
import math


#############################################
########      Utility functions    ##########
#############################################

def format_timedeltas(seconds, digits=1):
    ''' Function for formatting digits on timedeltas'''
    # Deal with Not a Times
    if seconds == pd.NaT:
        return pd.NaT
    isec, fsec = divmod(round(seconds*10**digits), 10**digits)
    if digits == 0:
        out = f'{timedelta(seconds=isec)}'
    elif digits > 0:
        out = f'{timedelta(seconds=isec)}.{fsec:0{digits}.0f}'
    else:
        raise Exception('{} digits not allowed'.format(digits))
    return out


def convert_size(size_in):
    '''Convert string to formatted-size string and total bytes
    '''
    size_name = ["B", "K", "M", "G", "T", "P", "E", "Z", "Y"]
    if type(size_in) == str:
        for i, x in enumerate(size_name):
            if x in size_in:
                size_name = size_name[i:]
                size_in = float(size_in.split(x)[0])
                size_bytes = size_in * 1024**i
                break
        if type(size_in) == str:
            return None, None

    if size_in == 0:
        return '0K', 0.

    i = int(math.floor(math.log(size_in, 1024)))
    p = math.pow(1024, i)
    s = round(size_in / p, 2)
    return f'{s:.2f}{size_name[i]:s}', size_bytes


def read_time_unix(infile):
    '''
    Read time info from time_unit.txt
    + Input timings in unix time (seconds since 1970.1.1)
    '''
    # Read the start time
    with open(infile) as f:
        firstline = f.readline().rstrip()
    sub_time = firstline[20:]

    # Convert to datetime
    sub_time = pd.to_datetime(sub_time, unit='s')

    # Pandas DF saving time and jobID info
    df = pd.read_table(infile, names=['Step','Job ID','Slurm array','Start','Finish','Elapsed'], delim_whitespace=True, comment='#')

    # Convert unix timestamps to datetime objects
    df['Start']     = pd.to_datetime(df['Start'],unit='s')
    df['Finish']    = pd.to_datetime(df['Finish'],unit='s')
    df['Elapsed']   = pd.to_timedelta(df['Elapsed'],unit='s')

    # Calculate the run time for each stage
    stages           = df['Step'].unique()
    stage_elapsed    = [] # elapse time
    stage_std        = [] # time std
    stage_mean       = [] # time mean; If mean + std is very different from elapsed that suggests lots of arrays have been queuing
    stage_start      = [] # start time
    stage_finish     = [] # end time
    stage_queue_time = [] # queue time
    stage_num_jobs   = [] # num of jobs in the array
    job_ids          = [] # job id

    for stage in stages:
        stage_df = df.loc[df['Step']==stage]
        num_jobs = len(stage_df.index)

        # Get Job ID for each stage
        job_ids.append(stage_df['Job ID'].unique()[0])

        # Find the earliest start and latest finish for each stage
        start = stage_df['Start'].min()
        finish = stage_df['Finish'].max()
        elapsed = finish - start
        std = stage_df['Elapsed'].std()
        mean = stage_df['Elapsed'].mean()

        # Save
        stage_elapsed.append(elapsed)
        stage_start.append(start)
        stage_finish.append(finish)
        stage_std.append(std)
        stage_mean.append(mean)
        stage_num_jobs.append(num_jobs)

    # Store summary data for each stage in a new dataframe
    data = {'Step':stages,
            'Start':stage_start,
            'Finish':stage_finish,
            'Total elapsed':stage_elapsed,
            'Array mean':stage_mean,
            'Array std':stage_std,
            'Num jobs':stage_num_jobs,
            'Job ID':job_ids
            }
    summary_df = pd.DataFrame(data)
    return summary_df, sub_time


def write_simple_runtime(summary_df):
    '''
    Function to write total run time to file in a convenient format
    Not used at all!
    '''
    fname='simple_runtime_summary.txt'

    # Convert timedelta into string
    summary_df['elapsed string'] = summary_df['Total elapsed'].apply(
            lambda x: f'{x.components.hours:02d}:{x.components.minutes:02d}:{x.components.seconds:02d}'
                if not pd.isnull(x) else '')
    out_df = pd.concat([summary_df['elapsed string'],pd.Series(total_run_time)])
    out_df.to_csv(fname, header=False, index=False)

    # Save pandas dataframe to txt file
    summary_df.to_csv('./log_files/summary_timings.csv',sep='\t')
    return


def estimate_cost(rsc_file, summary_df):
    '''
    Calculate resources used and estimate the HPC cost.
    ~~~ Caltech Resnick High Performance Computing Center ~~~
    Rate structure (might be updated): https://www.hpc.caltech.edu/rates
    '''
    # Core Hour Calculation: assume the Aggregate Spend is at the bracket of $6,501-$24,000
    rate = 0.008   # Fee per compute unit (1 CPU core hour = 1 computing unit)

    res_df = pd.read_table(rsc_file, header=0, delim_whitespace=True)
    res_df.columns = res_df.columns.str.replace('#', '') # Remove comment character from column names
    summary_df['CPUs']      = np.nan
    summary_df['GPUs']      = np.nan
    summary_df['CPU Units'] = np.nan
    summary_df['Cost ($)']  = np.nan

    # Loop through the stages and calculate the cost for each one
    # need summary_df and res_df to have corresponding rows - both referring to the same step of the procesing
    for index, row in res_df.iterrows():
        summary_df.loc[index,'CPUs'] = int(row['Ncpus_per_task'])  # pass num of CPUs to summary_df
        gpu_idxs = res_df['Gres'].to_numpy().nonzero()[0]          # find the stages use GPUs
        if index in gpu_idxs:
            summary_df.loc[index,'GPUs'] = res_df.loc[index,'Gres']
        else:
            summary_df.loc[index,'GPUs'] = 0

        hours     = summary_df.loc[index,'Array mean'].seconds / 3600
        num_jobs  = summary_df.loc[index,'Num jobs']
        cpu_units = (row['Ncpus_per_task'] + summary_df.loc[index,'GPUs']*10) * num_jobs * hours
        summary_df.loc[index,'CPU Units'] = cpu_units
        summary_df.loc[index,'Cost ($)']  = cpu_units*rate

    summary_df['CPUs'] = summary_df['CPUs'].astype(int)
    summary_df['GPUs'] = summary_df['GPUs'].astype(int)
    total_cost         = summary_df['Cost ($)'].sum()
    return total_cost


def plot_timings(summary_df, pic_file='cpu_wall_time.pdf'):
    fig, ax1 = plt.subplots()
    ax2      = ax1.twinx()
    width    = 0.4
    summary_df['CPU Units'].plot(x='Step',kind='bar',color='red',ax=ax1,width=0.4,position=0)
    # ax1.set_xticklabels(summary_df['Step'],rotation=45)
    # summary_df['Total elapsed'].plot(kind='bar',color='blue',ax=ax2,width=0.4,position=1)
    (summary_df['Total elapsed'].astype('timedelta64[s]')/3600).plot(x='Step',kind='bar',color='blue',ax=ax2,width=0.4,position=1)
    ax1.set_ylabel('CPU Hours',color='red')
    ax1.tick_params(axis='y',labelcolor='red')
    ax2.set_ylabel('Wall time (Hours)',color='blue')
    ax2.tick_params(axis='y',labelcolor='blue')
    ax2.set_xticklabels(summary_df['Step'],rotation=45)
    # plt.bar(x=summary_df.index+1,height=summary_df['CPU Units'])

    plt.tight_layout()
    plt.savefig(pic_file)
    return


def shorter_sacct_output(sacctfile, shortfile):
    '''
    Function to read from sacct output and condense the info to a new output .txt
    Return the result table as an array
    '''
    df = pd.read_fwf(sacctfile, delim_whitespace=True, skiprows=[1])
    df.rename(columns=lambda x: x.strip(), inplace=True)  # header trim whitespace
    res = [list(df.columns.values)+['SizeBytes']]

    job_id = None
    method = 'srun'
    for i, id_i in enumerate(df['JobID']):
        # Get the relevant lines for header & memory
        # Usage of sed, check: + https://stackoverflow.com/questions/1665549/have-sed-ignore-non-matching-lines
        #                      + https://unix.stackexchange.com/questions/33157/what-is-the-purpose-of-e-in-sed-command
        # For Slurm job steps: + https://stackoverflow.com/questions/52447602/slurm-sacct-shows-batch-and-extern-job-names
        if '.' not in id_i:
            if job_id: # change job in the job array, save the previous job
                MaxRSS, sbytes = convert_size(MaxRSS)
                MaxVMSize      = convert_size(MaxVMSize)[0]
                AveRSS         = convert_size(AveRSS)[0]
                AveVMSize      = convert_size(AveVMSize)[0]
                if sbytes:
                    row = [step_id,node,ReqMem,MaxRSS,MaxVMSize,AveRSS,AveVMSize,Elapsed,State,sbytes]
                    row[:-1] = [s.strip() for s in row[:-1]]  # strip all the strings
                    res.append(row)
            job_id = id_i
            ReqMem = df.loc[i,'ReqMem']
        else:
            if method == 'srun':
                if id_i.split(job_id.strip())[-1] in [f'.{x}' for x in range(10)]:
                    step_id, node, MaxRSS, MaxVMSize, AveRSS, AveVMSize, Elapsed, State =\
                        df.loc[i,'JobID'], df.loc[i,'NodeList'], df.loc[i,'MaxRSS'], df.loc[i,'MaxVMSize'],\
                        df.loc[i,'AveRSS'], df.loc[i,'AveVMSize'], df.loc[i,'Elapsed'], df.loc[i,'State']
            else:
                if id_i.split(job_id.strip())[-1] == '.batch':
                    step_id, node, MaxRSS, MaxVMSize, AveRSS, AveVMSize, Elapsed, State =\
                        df.loc[i,'JobID'], df.loc[i,'NodeList'], df.loc[i,'MaxRSS'], df.loc[i,'MaxVMSize'],\
                        df.loc[i,'AveRSS'], df.loc[i,'AveVMSize'], df.loc[i,'Elapsed'], df.loc[i,'State']
        # end of the job array, save the last job
        if i == len(df)-1:
            MaxRSS, sbytes = convert_size(MaxRSS)
            MaxVMSize      = convert_size(MaxVMSize)[0]
            AveRSS         = convert_size(AveRSS)[0]
            AveVMSize      = convert_size(AveVMSize)[0]
            if sbytes:
                row = [step_id,node,ReqMem,MaxRSS,MaxVMSize,AveRSS,AveVMSize,Elapsed,State,sbytes]
                row[:-1] = [s.strip() for s in row[:-1]]  # strip all the strings
                res.append(row)

    res = np.array(res)
    with open(shortfile, 'w') as ofile:
        fmt = '%16s %16s %8s %8s %10s %8s %10s %12s %12s'
        np.savetxt(ofile, res[:,:-1], fmt=fmt)
    return res


#############################################
########   Major analysis functions  ########
#############################################

def call_analyse_time(infile, rsc_file, pic_file, time_file):
    '''
    Major function to read and analyse timings and resources spent
    '''
    ## 1. Read from time_unix outputs from each job log
    summary_df, sub_time = read_time_unix(infile)

    ## 2. More time calculations
    # Calculate the overall wait time for the whole submission
    #  + note that we can have many wait times - every element of the array has to wait
    #  + we can compare the total elapsed to the mean and std for the array elements to see how long the stage took compared to the individual elements
    #  + this can tell us how many elements were running at one time on average
    total_run_time   = summary_df['Total elapsed'].sum()   # total run time
    end_time         = summary_df.iloc[-1]['Finish']       # finish time of job array
    total_time       = end_time - sub_time                 # time from submission to completion
    total_queue_time = total_time - total_run_time         # the total queue time

    # Calculate the queue time in between each stage (start of row - finish of previous row)
    #  + this is just the queue time between stages.
    #  + it doesn't take into account the queue time for each array element during processing
    #  + so this is basically the time where nothing is running at all
    init_q = summary_df.iloc[0]['Start'] - sub_time       # Initial queue time
    summary_df['Queue time'] = summary_df['Start'] - summary_df['Finish'].shift()
    summary_df.at[0,'Queue time'] = init_q


    ## 3. Estimate the HPC cost
    total_cost = estimate_cost(rsc_file, summary_df)


    ## 4. Write & Print summaries

    # submission local time
    fmt   = "%Y-%m-%d %H:%M:%S" # format in pandas datetime
    tzone = 'US/Pacific'
    sub_time_obj       = datetime.strptime(str(sub_time), fmt).replace(tzinfo=timezone.utc)
    sub_time_local_obj = sub_time_obj.astimezone(ptimezone(tzone)) # convert to local time zone obj
    sub_time_local     = sub_time_local_obj.strftime(fmt)         # format the above datetime

    print('#####################################################')
    print('# Summary timings')
    print('# Job submitted at:    {} (UTC)'.format(sub_time))
    print('# Job submitted at:    {} ({})'.format(sub_time_local, tzone))
    print('# Total time:          {}'.format(total_time))
    print('# Total run time:      {}'.format(total_run_time))
    print('# Total queue time:    {}'.format(total_queue_time))
    print('# Esimated cost:       ${:.2f}'.format(total_cost))
    print('#####################################################')
    # save timings to file
    with open(time_file,'w') as f:
        f.write('#####################################################\n')
        f.write('# Summary timings\n')
        f.write('# Job submitted at:    {} (UTC)\n'.format(sub_time))
        f.write('# Job submitted at:    {} ({})\n'.format(sub_time_local, tzone))
        f.write('# Total time:          {}\n'.format(total_time))
        f.write('# Total run time:      {}\n'.format(total_run_time))
        f.write('# Total queue time:    {}\n'.format(total_queue_time))
        f.write('# Esimated cost:       ${:.2f}\n'.format(total_cost))
        f.write('#####################################################\n')
        f.write('\n')

    # Change formatting of timedeltas
    for index, row in summary_df.iterrows():
        summary_df.loc[index,'Array mean'] = format_timedeltas(row['Array mean'].seconds,digits=0)
        # Replace NaTs with 0
        if pd.isnull(row['Array std']):
            summary_df.loc[index,'Array std'] = 0
        else:
            summary_df.loc[index,'Array std'] = format_timedeltas(row['Array std'].seconds,digits=0)

    # Write to formatted table
    use_cols = ['Step','Num jobs', 'Start', 'Finish', 'Total elapsed', 'Queue time', 'Array mean', 'Array std', 'CPUs', 'GPUs', 'Cost ($)']
    with open(time_file, 'a') as f:
        summary_df.to_string(f,columns=use_cols)

    # 5. Plot some figures
    plot_timings(summary_df, pic_file)

    return summary_df


def call_analyse_max_mem_use(jobIDs, stageNames, mem_dir='./mem_usage/', mem_file='max_mem_usage.txt'):
    '''
    Major function to read from sacct output and find the max mem usage job
    + might also need to look at 'State' if we're having job failures
    + assuming that we're using topsStack with slurm arrays for each step, each step having a separate SLURM job id
    + todo: we get an easier output format (showing usage and efficiency for memory and CPU) using 'seff <job_id>_<array_index>'
    '''
    mem_file = mem_dir + mem_file
    if not os.path.exists(mem_dir):
        os.makedirs(mem_dir)

    sum_row = [['JobSeq', 'stageName', 'JobID_arrayNo', 'ReqMem', 'MaxRSS', 'AveRSS', 'Elapsed']]

    # loop over jobs
    for job_seq, jobID in enumerate(jobIDs):
        job_seq += 1
        ## 1. Run sacct command to print details
        sacctfile = os.path.normpath(f'{mem_dir}/{job_seq}_{jobID}_sacct.txt')
        shortfile = os.path.normpath(f'{mem_dir}/{job_seq}_{jobID}.txt')
        os.system(f'sacct -j {jobID} --format=JobID%20,NodeList,ReqMem,MaxRSS,MaxVMSize,AveRSS,AveVMSize,Elapsed,State%12 > {sacctfile}')
        # + https://www.hpc.caltech.edu/documentation/slurm-commands
        # + https://srcc.stanford.edu/sites/g/files/sbiybj25536/files/media/file/sherlock_onboarding-11-2022.pdf

        ## 2. Read sacct output and condense it
        res = shorter_sacct_output(sacctfile, shortfile)

        ## 3. find the jobID of max usage in the whole job
        max_idx = np.argsort(res[1:,-1].astype(float))[-1]+1   # sort based on size_bytes
        sum_row.append([job_seq, stageNames[job_seq-1], res[max_idx,0], res[max_idx,2], res[max_idx,3], res[max_idx,5], res[max_idx,7]])

    # save the summary to max_mem_usage.txt
    with open(mem_file, 'w') as ofile:
        fmt = '%7s %32s %20s %10s %10s %10s %12s'
        ofile.write('# Maximum memory usage for each stage in the processing\n')
        np.savetxt(ofile, np.array(sum_row), fmt=fmt)
    return


#######################################################################################
def main():
    # Paths
    base_dir  = './'
    #--------------- for timings ---------------------------
    infile    = base_dir + 'log_files/time_unix.txt'
    rsc_file  = base_dir + 'resources.cfg'
    pic_file  = base_dir + 'cpu_wall_time.pdf'
    time_file = base_dir + 'formatted_summary_timings.txt'
    #--------------- for memory ----------------------------
    mem_dir   = base_dir + 'mem_usage/'

    ## Step 1:
    summary_df = call_analyse_time(infile, rsc_file, pic_file, time_file)
    jobIDs, stages = summary_df['Job ID'], summary_df['Step']

    ## Step 2:
    call_analyse_max_mem_use(jobIDs, stages, mem_dir=mem_dir)

#######################################################################################

if __name__ == '__main__':
    main()
    print('Normal end of script~')
