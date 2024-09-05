# Scripts to submit [ISCE2](https://github.com/isce-framework/isce2) [topsStack](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack) steps as chained SLURM jobs

### Pre-requisites:

Have ISCE2 topsStack processor [installed](https://github.com/earthdef/sar-proc), and make sure [`stackSentinel.py`](https://github.com/isce-framework/isce2/blob/main/contrib/stack/topsStack/stackSentinel.py) can run (`stackSentinel.py -h`)

Install reportseff from: https://pypi.org/project/reportseff/

### Overview:

Each job uses SLURM arrays to manage the processing that can be done in parallel. This avoids the issue with wasting resources, hopefully deals better with very large numbers of jobs.

The `resources.cfg` file gives the resources allocated to each array element, not to the whole job step combined. Different resource files are for different sized jobs - `resources_array_full_eff.cfg` is the efficient ('eff') allocation of resources for the full T115a track (25 to 32N)

# Brief workflow
1. Copy the `hpc_topsStack` folder to the track main directory:
    ```bash
    cp -r ~/tools/isce-proc/src/isce_proc/contrib/hpc_topsStack .
    ```

2. Download SLC, DEM, auxially files
    - SLC: you can use [`ssara_federated_query.py`](https://www.unavco.org/gitlab/unavco_public/ssara_client). Or you can use `script/asf_download.py`.
    - DEM: Cunren's script `download_dem.py`
    - AUX files: https://s1qc.asf.alaska.edu//aux_cal/

4. Pre-select SLCs: Run [`s1_select_ion.py`](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack#1-select-the-usable-acquistions) to get the starting ranges for each subswath for each frame (SLC). Save output to a txt file. You will find files that are suggested to be removed been moved to the `not_used` folder. The rest zip files will be the input to `stackSentinel.py`.
    ```bash
    s1_select_ion.py -dir ./SLC -sn 18.8 20.4 > s1_select_ion.txt
    ```

6. Stack processor configuration: edit the template file, e.g., `AqabaSenAT087.txt`.
    ```bash
    # create a symbolic link SLC/ to the data zip files, ex:
    ln -s ../data/slc/s1a SLC
    ```

8. run [`run_isce_stack.py`](https://github.com/earthdef/sar-proc/tree/main/tools) (a wrapper of `stackSentinel.py`) to generate the run files for the stack processor.
    ```bash
    run_isce_stack.py AqabaSenAT087.txt
    ```
    Outputs:
        - Directroy `./configs` containing stackSentinel configs
        - Directroy `./run_files` with run files and corresponding *.job SLURM scripts for submitting to the computing nodes.
        - SAFE_files.txt: lists usable zip files
        - pairs_diff_starting_ranges.txt: lists ionosphere phase estimation pairs with different platforms and swath starting ranges.

9. Post-select some skipped pairs if you want. This will update the run_files for forming regular interferograms.
    ```bash
    python script/s1_select_runpairs.py
    ```

10. Edit the `resources.cfg` for resourse allocation.
    NB: for a full long track in e.g., Makran (>7 lat deg)
        - run_16_unwrap         set to 3 hr for long tracks; also needs ~16GB of memory
        - run_20_unwrap_ion     set to 3 hr for long tracks
        - run_23_filtIon        set memory usage to 30G for lon tracks

11. Generate the slurm job scripts.
    ```bash
    # Edit the $TRACK in stackSenBatch.sh
    # Then run it
    bash stackSenBatch.sh
    ```
    Outputs:
        - `run_files/*.job` for each run_file

12. Submit all the jobs. Now can close your terminal and wait for completing email.
    ```bash
    bash ./run_files/submit_chained_dependencies.sh
    ```

13. If you need to re-run and reset the processing:
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

# Additional notes

## compute Ion
Modify `run_22_computeIon` for pairs with different starting ranges. Those pairs have sub-swath ionosphere igrams created separately, then merged together. The commands for merging, i.e., `mergeSwathIon.py`, shoud be conducted after all the sub-swath igrams are computed (`computeIon.py`). This can have issues when parallelizing commands in SLURM. You can move all the commands with `mergeSwathIon.py` to the bottom of the run file and modify the job script `run_22_computeIon.job` to run them separately. This step will only take a few minutes of computation. It is the manual work that is annoying.

## `NCPUS_PER_TASK` for `run_01` depends on `NUM_PROCESS_4_TOPO`
It looks like this variable `NUM_PROCESS_4_TOPO` gets passed to a python multiprocessing pool, where it's used to process the number of bursts we have in the reference SLC (see topsStack/topo.py). In theory this means we'll get the fastest speeds if we set it equal to the number of bursts

But NOTE - the relevant step (run_01_unpack_topo_reference) has to be run on a single node, so we can't use more than 32 or 56 CPUs (https://www.hpc.caltech.edu/resources).

If CPUS_PER_TASK=4, max NUM_PROCESS_4_TOPO=7 or 8

This variable gets passed to python multiprocess pool. We should give it the same number of CPUs I think? If we don't set it, it's automatically set to NUM_PROCESS by ISCE

## Re-submit failed jobs
`analysis_time.py`: If re-submitting jobs, go ahead and erase the redundant header rows in the log files `time_unix.txt` and `timing.txt`.

## Disk Quota
Keep only merged files given limited disk quota (stackSentinel.py -V False). I usually turn OFF the virtual merge to let topsStack generate the merged SLC in full resolution, so that I could keep the entire merged folder, not the coreg_secondarys . I found the merged single-file SLC easier to play with, e.g. for ampcor. Meaning, once we have the merged/SLC/ we can remove all the burst-level files under the main directory: coarse_interferograms, interferograms, geom_reference, secondarys

## might-be-obsolete notes
NUM_PROCESS=30 # Number of commands between 'wait' statements in run files.
NOW that we're using SLURM arrays, we don't need to use & and wait.
I think we should set this to larger than the largest number of commands in an individual step, then let srun sort the starting of tasks.
