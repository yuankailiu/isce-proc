# Scripts to submit [ISCE2](https://github.com/isce-framework/isce2) [topsStack](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack) steps as chained SLURM jobs

## Contribution (whoever made all these feasible):
2019 to 2023.
- Yuan-Kai Liu, Oliver Stephenson, Cunren Liang

## Pre-requisites:

Have ISCE2 topsStack processor [installed](https://github.com/earthdef/sar-proc), and make sure [`stackSentinel.py`](https://github.com/isce-framework/isce2/blob/main/contrib/stack/topsStack/stackSentinel.py) can run (`stackSentinel.py -h`)

Install reportseff from: https://pypi.org/project/reportseff/

## Overview:

Each job uses SLURM arrays to manage the processing that can be done in parallel. This avoids the issue with wasting resources, hopefully deals better with very large numbers of jobs.

The `resources.cfg` file gives the resources allocated to each array element, not to the whole job step combined. Different resource files are for different sized jobs - `resources_array_full_eff.cfg` is the efficient ('eff') allocation of resources for the full T115a track (25 to 32N)

## Brief workflow
### A. Basic preparation:
1. Copy the `hpc_topsStack` folder to the track main directory:
    ```bash
    cp -r ~/tools/isce-proc/src/isce_proc/contrib/hpc_topsStack .
    ```

2. Download SLC, DEM, auxially files
    - SLC: you can use [`ssara_federated_query.py`](https://www.unavco.org/gitlab/unavco_public/ssara_client). Or you can use `script/asf_download.py`, which uses this [ASF API](https://github.com/asfadmin/Discovery-asf_search).
    - DEM: Cunren Liang's script `download_dem.py`, under the scripts/ folder.
    - AUX files: https://s1qc.asf.alaska.edu//aux_cal/
    - Orbits: If you want to pre-download the orbits for any reason, [`fetchOrbit.py`](https://github.com/isce-framework/isce2/blob/main/contrib/stack/topsStack/fetchOrbit.py) in stack processor can be called manually. Otherwise, I think [`stckSentinel.py`](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack) also call it on the fly.
    - Alternatively, you can use Scott Staniewicz’s EOF: https://github.com/scottstanie/sentineleof

3. Pre-select SLCs: Run the topsStack's [`s1_select_ion.py`](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack#1-select-the-usable-acquistions) to get the starting ranges for each subswath for each frame (SLC). Save output to a txt file. You will find files that are suggested to be removed been moved to the `not_used` folder. The rest zip files will be the input to `stackSentinel.py`.
    ```bash
    s1_select_ion.py -dir ./SLC -sn 18.8 20.4 > s1_select_ion.txt
    ```

### B. Generate the stackSentinel's run files
1. You can simply use the typical [`stackSentinel.py`] script where you installed with ISCE2.

2. If you want to use [Yunjun's wrapper on stackSentinel](https://github.com/radar-science/isce-proc):
   1. you need the configuration file: edit the template file, e.g., `AqabaSenAT087.txt`.
    ```bash
    # create a symbolic link SLC/ to the data zip files, ex:
    ln -s ../data/slc/s1a SLC
    ```

   2. run [`run_isce_stack.py`](https://github.com/earthdef/sar-proc/tree/main/tools) (a wrapper of `stackSentinel.py`) to generate the run files for the stack processor.
    ```bash
    run_isce_stack.py AqabaSenAT087.txt
    ```
    Outputs:
        - Directroy `./configs` containing stackSentinel configs
        - Directroy `./run_files` with run files and corresponding *.job SLURM scripts for submitting to the computing nodes.
        - SAFE_files.txt: lists usable zip files
        - pairs_diff_starting_ranges.txt: lists ionosphere phase estimation pairs with different platforms and swath starting ranges.


### C. Do some organizing on your run files if needed.

1. Post-select some skipped pairs if you want. This will update the run_files for forming regular interferograms,(from steps including and after run_13, I think).
    ```bash
    python script/s1_select_runpairs.py
    ```

### D. Do HPC Slurm job creation and submission

1.  Edit the `resources.cfg` for resourse allocation.
    NB: for a full long track in e.g., Makran (>7 lat deg)
        - run_16_unwrap         set to 3 hr for long tracks; also needs ~16GB of memory
        - run_20_unwrap_ion     set to 3 hr for long tracks
        - run_23_filtIon        set memory usage to 30G for lon tracks

2.  Generate the slurm job scripts.
    ```bash
    # Edit the $TRACK in stackSenBatch.sh
    # Then run it
    bash stackSenBatch.sh
    ```
    Outputs (all written by `scripts/write_slurmJobs.py`, which `stackSenBatch.sh` calls):
        - `run_files/*.job` for each run_file
        - `run_files/disk_usage.job`, submitted after each step outside the dependency chain
        - helper scripts copied into `run_files/`, incl. `clean_topsStack.py`; the deletion
          lines in the *.job files are commented out unless you pass `--clean`
        - run `python run_files/clean_topsStack.py` to see when each file type can be deleted

3.  Submit all the jobs. Now can close your terminal and wait for completing email.
    ```bash
    bash ./run_files/submit_chained_dependencies.sh
    ```

4.  If you need to re-run and reset the processing:
    If you want to try different parameters in stack processing, or adding new data, sometimes you want to re-process the whole stack from scratch. I think stackSentinle.py will prevent you from simply re-run the stuff without cleaning up the whole folder. So you have to rename or move the current products folder in order to re-run the code.
    ```bash
    # ------ Copy and paste the following the command to reset the process direction ----
    rm -rf baselines/ configs/ coarse_interferograms/ coreg_secondarys/ ESD/ geom_reference/ interferograms/ merged/ misreg/ reference/ run_files/ secondarys/ stack/

    # ------ Can copy and save the configs/logs/any docs before deleting ----
    mkdir -p docs/run_files/
    mv inputs scripts configs pic stackSenBatch.sh docs/                # for HPC Slurm processing
    cp *.cfg *.txt *.log *.pdf *.png *.md docs/
    cd run_files/
    cp *.cfg *.txt *.log *.pdf *.png *.md ../docs/run_files/
    mv log_files* job_* preselect mem_usage run_* ../docs/run_files/    # for HPC Slurm processing
    cd .. && mv docs ../
    rm -rf baselines/ configs/ coarse_interferograms/ coreg_secondarys/ ESD/ geom_reference/ interferograms/ merged/ misreg/ reference/ run_files/ secondarys/ stack/

    # ------ If you want to keep the files but re-create run files after changing parameters ----
    # Change the following tow folders to avoid update checking
    mv run_files run_files_bak
    mv coreg_secondarys coreg_secondarys_bak

    # Run your run_isce_stack.py with new params, this will create new run_files/, add/overwrite configs/
    run_isce_stack.py AqabaSenDT123.txt

    # Change the coreg_secondary back
    mv coreg_secondarys_bak coreg_secondarys

    # Now go ahead run_files/ and run your new run files with new configs
    ```

### E. Ionosphere phase delay correction and other caveats:

If you do ionosphere split-band correction (by providing the ion_params.txt to the stackSentinel code), the run_file/ folder will have up to run_28 steps (rather than up to run_16 for regular processing). Please check [Cunren Laing's paper](https://ieeexplore.ieee.org/document/8706258) for credits and details. And my Slurm generation script, `stackSenBatch.sh`, still handles that to run_28, no problem.

There are two stages of ionosphere corrections (you can only do stage 1 if you are already happy with it):

Stage 1: run_17 to run_24 is to deal with smooth ionosphere phase screen across the entire scene. This is based on a PR implemented in ISCE2 [#326](https://github.com/isce-framework/isce2/pull/326) by Cunren Liang.

Stage 2: run_25 to run_28 is to deal with the intra-burst ramps introduced by the azimuthal shifts caused by the ionosphere delay. This is implemented in PR [#600](https://github.com/isce-framework/isce2/pull/600) by Cunren Liang. Check the similar results in [his paper in his Fig 5](https://ieeexplore.ieee.org/document/8706258).

If you need to run Stage 2 burst ramp corrections. Here are Cunren's note:
> I developed an efficient method to estimate azimuth burst ramps caused by ionosphere. It’s fast. It adds a few new steps to the original workflow. All newly added steps are after the original processing steps. However, it requires ESD turned on in topsStack processing. So for your early processing results, if you did turn ESD on, you don’t have to reprocess the data. You can rerun stackSentinel.py to generate the required run files, and only run the newly added run files to compute azimuth burst ramps.


#### Ionosphere making:
Make sure you mask some bad pixels (unwrapping errors) before filtering the ionoshpere phase. Filtering is applied in run_23. You need to modify the ion_params.txt and re-run run_22 to apply the masking. Then you can safely run run_23 o filter, and so on...

I have updated some isce2 stack processor code to do better masking for unwrapping errors. But not ready to clean up and make them public yet. Or maybe I did on my personal branch of isce2, but i forget at the moment.


#### Always double check

Ionosphere phase estimates need to be babysit. Always treat them with care and patient! And expect them to go crazy and having tantrum 50\% of the time.


## Additional notes

There are notes from long time ago. Not updated. Some are still valid.

### compute Ion
Modify `run_22_computeIon` for pairs with different starting ranges. Those pairs have sub-swath ionosphere igrams created separately, then merged together. The commands for merging, i.e., `mergeSwathIon.py`, shoud be conducted after all the sub-swath igrams are computed (`computeIon.py`). This can have issues when parallelizing commands in SLURM. You can move all the commands with `mergeSwathIon.py` to the bottom of the run file and modify the job script `run_22_computeIon.job` to run them separately. This step will only take a few minutes of computation. It is the manual work that is annoying.

### `NCPUS_PER_TASK` for `run_01` depends on `NUM_PROCESS_4_TOPO`
It looks like this variable `NUM_PROCESS_4_TOPO` gets passed to a python multiprocessing pool, where it's used to process the number of bursts we have in the reference SLC (see topsStack/topo.py). In theory this means we'll get the fastest speeds if we set it equal to the number of bursts

But NOTE - the relevant step (run_01_unpack_topo_reference) has to be run on a single node, so we can't use more than 32 or 56 CPUs (https://www.hpc.caltech.edu/resources).

If CPUS_PER_TASK=4, max NUM_PROCESS_4_TOPO=7 or 8

This variable gets passed to python multiprocess pool. We should give it the same number of CPUs I think? If we don't set it, it's automatically set to NUM_PROCESS by ISCE

### Re-submit failed jobs
`analysis_time.py`: If re-submitting jobs, go ahead and erase the redundant header rows in the log files `time_unix.txt` and `timing.txt`.

### Disk Quota
Keep only merged files given limited disk quota (stackSentinel.py -V False). I usually turn OFF the virtual merge to let topsStack generate the merged SLC in full resolution, so that I could keep the entire merged folder, not the coreg_secondarys . I found the merged single-file SLC easier to play with, e.g. for ampcor. Meaning, once we have the merged/SLC/ we can remove all the burst-level files under the main directory: coarse_interferograms, interferograms, geom_reference, secondarys

### might-be-obsolete notes
NUM_PROCESS=30 # Number of commands between 'wait' statements in run files.
NOW that we're using SLURM arrays, we don't need to use & and wait.
I think we should set this to larger than the largest number of commands in an individual step, then let srun sort the starting of tasks.
