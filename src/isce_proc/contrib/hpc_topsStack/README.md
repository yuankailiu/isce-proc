# Sentinel-1 [ISCE2 topsStack](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack) on Slurm: `topsstack.py`

Credits: Yuan-Kai Liu, Oliver Stephenson, Cunren Liang (2019–2026).

One command, `topsstack.py <subcommand> <track template>`, runs a track from the SLC search to the
MintPy inputs. Everything track-specific lives in the template (e.g. `ChileSenAT076.txt`, kept in the
stack directory); keys left `auto` are derived. Every subcommand runs from the stack directory, logs
its commands to `logs/<subcommand>_<date>.log`, and is safe to re-run.

Requires ISCE2 with topsStack (`stackSentinel.py -h`), MintPy, `asf_search`, and an Earthdata entry in
`~/.netrc`. Optional: `reportseff`.

## A new track

```bash
mkdir -p mytrack/hpc_topsStack && cd mytrack/hpc_topsStack
cp <an existing template> ChileSenAT076.txt     # edit isce.boundingBox, isce.demFile, ...
mkdir -p inputs && cp <isce-proc>/src/isce_proc/contrib/hpc_topsStack/inputs/ion_param.txt inputs/   # for ionosphere
ln -s ../data SLC
topsstack.py show     ChileSenAT076.txt          # check the resolved settings

topsstack.py search   ChileSenAT076.txt          # ASF search          -> ../data/search_results.csv/kml
topsstack.py download ChileSenAT076.txt --slurm  # zips, 8 Slurm shards, resumable; --verify for CRC
topsstack.py inspect  ChileSenAT076.txt          # s1_version.txt, s1_slice.txt, epochs_latlon.png, extent
topsstack.py select   ChileSenAT076.txt          # s1_select_ion.py: unusable slices -> not_used/
topsstack.py dem      ChileSenAT076.txt          # DEM + water body (if isce.demFile is not there yet)
topsstack.py stack    ChileSenAT076.txt          # run_isce_stack.py: configs/, run_files/
topsstack.py stack    ChileSenAT076.txt --ion-config        # filtIon/burstRampIon keys; computeIon order
topsstack.py stack    ChileSenAT076.txt --select-pairs 5 10 # optional: thin the interferogram network
topsstack.py jobs     ChileSenAT076.txt          # run_files/*.job (+ gate.job, disk_usage.job, helpers)
topsstack.py submit   ChileSenAT076.txt          # the whole chain; -s/-e/-l for a range or list of steps
```

While it runs, and after:

```bash
topsstack.py status   ChileSenAT076.txt 13-16    # rows per step with missing/incomplete outputs
topsstack.py status   ChileSenAT076.txt 15 --rerun --submit   # rerun only those rows, re-link the chain
topsstack.py clean    ChileSenAT076.txt          # what may be deleted when, for this stack
topsstack.py clean    ChileSenAT076.txt esd coreg_overlap           # dry run (lists files, sizes)
topsstack.py clean    ChileSenAT076.txt esd coreg_overlap --delete --reuse
topsstack.py report   ChileSenAT076.txt          # time, CPU, memory, cost, size per step
topsstack.py plot     ChileSenAT076.txt ion      # or unw, baselines, network  -> pic/
topsstack.py export   ChileSenAT076.txt --dry-run   # copy products + records to hpc.exportDir
```

## The template

`isce.*` keys go to `stackSentinel.py` through `run_isce_stack.py` (see `topsstack.py -h` and
`utils/config.py` for all keys and defaults). The others are read by `topsstack.py` only:

| group | keys (all optional) | derived when `auto` |
|---|---|---|
| `asf.*` | `dataDir wkt bbox relativeOrbit flightDirection platforms start end processes shards` | orbit and direction from the name (`...SenAT076`), AOI from `isce.boundingBox`, data in `../data` |
| `dem.*` | `dir snwe buffer waterBody` | integer box = bounding box + 1° |
| `select.*` | `southNorth minAcq numConnections bridge` | S/N from the bounding box |
| `ion.*` | `wbdFile maskFile iteration fill swathAlign burstRampMask` | water body next to `isce.demFile`, same box |
| `hpc.*` | `track account mail ompTopo clean gate gateRetries exportDir costPerCpuHour` | `a076`, `simonsgroup`, `$USER@caltech.edu` |

Per-step Slurm resources are in `run_files/resources.cfg` (copied from `inputs/` on the first `jobs`;
edit it there). `batch` is the number of array tasks running at once.

## How the chain is protected

- **Exit status:** each task exits with its command's status, so a failed command is `FAILED` in Slurm
  and the `afterok` chain stops. `COMPLETED` is still not proof of outputs; `status` is.
- **`status`** reads each row's outputs from its config and checks existence and size against the
  `.xml`. A missing binary whose `.xml` is still there is reported as `cleaned`, not failed.
- **Gate (`hpc.gate = yes`):** after each step, a small job runs `status --gate`: it reruns bad rows up
  to `hpc.gateRetries` times and lets the next step start only when every row is good (else the chain
  stops and you get a FAIL mail). Parts of a step then chain with `afterany`.
- **`clean --delete`** deletes a file type only if its last reader (the kill-after step in the
  `clean` table) has all its outputs. The table is derived from the stack: bursts read from the SLC zips
  through VRTs (always, in topsStack), `virtual_merge`, and whether ionosphere steps exist. For example,
  the zips are needed until `subband_and_resamp` (step 17) with ionosphere, else until step 13.
- **Disk use** is recorded after each step by `disk_usage.job`, outside the chain.
- `hpc.clean = yes` activates the deletion lines written into the job files (commented otherwise).

## Ionosphere (steps 17–28)

- Steps 17–24 estimate the smooth ionospheric phase from range sub-bands
  ([Liang et al., 2019](https://ieeexplore.ieee.org/document/8706258); ISCE2 PR #326). Step 17 re-reads
  the raw secondary bursts from the zips, so keep them until it has finished.
- Steps 25–28 estimate the azimuth phase ramp per burst caused by the ionospheric azimuth shift
  (PR #600). They need ESD applied during coregistration: step 27 subtracts the ESD-type part of the
  swath-mean shift, which ESD has already corrected. They do not read the ESD files themselves.
- `stack --ion-config` sets the filtIon keys and moves the `mergeSwathIon.py` rows of
  `run_22_computeIon` behind the `computeIon.py` rows; `jobs` gives them their own job part, so each
  merge runs after the sub-swath results of its pair exist.
- Mask unwrapping errors before filtering (step 23): set `ion.maskFile` (e.g. from `otsu_masking.py`),
  re-run `stack --ion-config`, then re-run from step 22. Always look at the ionosphere products
  (`plot ... ion`); they need checking.

## Notes

- `submit` calls `sbatch` two or three times per step (step, gate, disk usage); on a busy controller
  that can take ~30 s per call, so run it in the background (`nohup topsstack.py submit ... &`).
- GPU steps request `--gres=gpu:<hpc.gpuType>:<n>` (Slurm here rejects a count without a type);
  with `isce.useGPU = no` no GPU is requested at all.
- `run_01_unpack_topo_reference` runs a Python pool of `Ncpus_per_task / hpc.ompTopo` processes
  (one per burst is fastest), each with `hpc.ompTopo` OpenMP threads, on one node (≤ 56 CPUs here).
- Using this version: `export ISCE_PROC_HOME=~/tools/isce-proc-v2` before loading the environment
  (`~/tools/conda-envs/isce2/config.rc`). The previous scripts are in `scripts/obsolete/`, with the old
  README (`README_v1.md`).
