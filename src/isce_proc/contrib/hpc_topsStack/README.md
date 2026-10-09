# Sentinel-1 [ISCE2 topsStack](https://github.com/isce-framework/isce2/tree/main/contrib/stack/topsStack) on Slurm: `topsstack.py`

Credits: Yuan-Kai Liu, Oliver Stephenson, Cunren Liang (2019–2026).

One command, `topsstack.py <subcommand> <track template>`, runs a track from the SLC search to the
MintPy inputs. Everything track-specific lives in the template (e.g. `ChileSenAT076.txt`, kept in the
stack directory); keys left `auto` are derived. Every subcommand runs from the stack directory, logs
its commands to `logs/<subcommand>_<date>.log`, and is safe to re-run.

Requires ISCE2 with topsStack (`stackSentinel.py -h`), MintPy, `asf_search`, and an Earthdata entry in
`~/.netrc`. Optional: `reportseff`.

Everything of this workflow is in this directory:

```
hpc_topsStack/
├── topsstack.py     the one command (put this directory on PATH)
├── commands/        code of the subcommands: cli, config, data, stack, report, export, plot
├── scripts/         tools the subcommands run, also copied into <stack>/run_files/:
│                    write_slurmJobs.py, submit_chained_dependencies.sh, check_topsStack.py,
│                    clean_topsStack.py, s1_version.py, plot_imgs.py, ...
├── inputs/          defaults: resources.cfg, slurm.job, ion_param.txt, example template
└── download_dem.sh
```

It uses `run_isce_stack.py` and `utils/` of `src/isce_proc/` (shared with the rest of isce-proc).

## Step by step: a new track

Example: Chile, ascending track 120, stack in `chile/a120/hpc_topsStack`. Replace the names with yours.

### 0. Use this version (each new shell)

```bash
source ~/tools/conda-envs/isce2/config.rc              # the usual environment
export PATH=~/tools/isce-proc-v2/src/isce_proc/contrib/hpc_topsStack:$PATH   # topsstack.py (branch workflow-v2) first
which topsstack.py   # -> ~/tools/isce-proc-v2/src/isce_proc/contrib/hpc_topsStack/topsstack.py
```

`topsstack.py` always uses the `isce_proc` code next to it, also in the scripts it starts, so the
`ISCE_PROC_HOME` of `config.rc` (the old version) does not matter. Old tracks keep working with their
own `run_files/` copies. To go back, open a new shell without the `export PATH` line.

`topsstack.py -h` and `topsstack.py CMD -h` list the options and the template keys with their defaults,
colored on a terminal (Python 3.14 argparse theme); `NO_COLOR=1` turns colors off.

### 1. Make the stack directory and the template

```bash
mkdir -p /resnick/groups/simonsgroup/ykliu/chile/a120/hpc_topsStack
cd       /resnick/groups/simonsgroup/ykliu/chile/a120/hpc_topsStack
cp ../../a076/hpc_topsStack/ChileSenAT076.txt ChileSenAT120.txt
mkdir -p inputs && cp ~/tools/isce-proc-v2/src/isce_proc/contrib/hpc_topsStack/inputs/ion_param.txt inputs/
ln -s ../data SLC                                      # SLC zips go to ../data
```

Edit `ChileSenAT120.txt`: at least `isce.boundingBox` (S, N, W, E), `isce.demFile`, the looks, and
the workflow (`isce.workflow`; `isce.paramIonFile = ./inputs/ion_param.txt` turns the ionosphere on). The
template name gives the orbit and direction (`...SenAT120` = ascending, relative orbit 120). New keys
(`asf.*`, `dem.*`, `select.*`, `ion.*`, `hpc.*`, table below) are optional. Useful ones:

```
hpc.gate      = no         # only to turn off the gate (default yes: checks between steps, mail only on a stop and at the end)
asf.start     = 2014-10-01
```

Check what the template resolves to:

```bash
topsstack.py show ChileSenAT120.txt
```

### 2. Find and download the SLCs

```bash
topsstack.py search   ChileSenAT120.txt              # optional: -> ../data/search_results.csv / .kml
topsstack.py download ChileSenAT120.txt --slurm 8    # 8 Slurm array tasks; watch: squeue -u $USER
topsstack.py download ChileSenAT120.txt --dry-run    # after they end: should list 0 missing
topsstack.py download ChileSenAT120.txt --verify     # CRC check of every zip (optional, slow)
```

Re-run `download --slurm 8` if some tasks ended early; it continues where it stopped.
`download` runs `search` itself if `search_results.csv` is missing or was made from other `asf.*`
settings or another template (data dir shared by several stacks; see `../data/search_params.txt`).
Run `search` alone to look at the list first. Each Slurm task uses 1 CPU.

### 3. Look at the SLCs, drop the unusable ones

```bash
topsstack.py inspect ChileSenAT120.txt     # ../data/s1_version.txt, s1_slice.txt, epochs_latlon.png
topsstack.py select  ChileSenAT120.txt     # s1_select_ion.py: unusable slices -> ../data/not_used/
                                          # + pic/select_network.png (redraw: plot ... select)
```

Open `epochs_latlon.png` to check the coverage of each date before going on.

### 4. DEM and water body

```bash
topsstack.py dem ChileSenAT120.txt         # only if isce.demFile does not exist yet
```

### 5. Configs and run files

```bash
topsstack.py stack ChileSenAT120.txt                    # stackSentinel.py -> configs/, run_files/
topsstack.py stack ChileSenAT120.txt --ion-config       # ionosphere only: water body, masks
topsstack.py stack ChileSenAT120.txt --select-pairs 5 10   # optional: keep 5 nearest pairs + 10-date bridge
                                                        # (automatic in `stack` if select.numConnections is set)
topsstack.py stack ChileSenAT120.txt --add-pairs pairs.txt  # optional: extra pairs, e.g. same-season bridges over a data gap
```

Large stacks: `hpc.fuseMerge = yes` runs generate_burst_igram inside merge_burst_igram, pair by pair, and deletes
each pair's burst interferograms after its merge, so the peak disk use is the running tasks, not all pairs.

```bash
```

`--ion-config` sets the filtIon keys (`wbdfile`, `iteration 5`, `fill nearest`; `swath_align` only if
`ion.swathAlign = yes`), the burstRampIon mask, and puts the `mergeSwathIon.py` rows of step 22 last.
Safe to re-run.

### 6. Slurm jobs and submission

```bash
topsstack.py jobs   ChileSenAT120.txt                   # run_files/*.job, gate.job, disk_usage.job
nohup topsstack.py submit ChileSenAT120.txt -e 23 > logs/submit.out 2>&1 &   # with ionosphere: stop before 24
nohup topsstack.py submit ChileSenAT120.txt > logs/submit.out 2>&1 &         # without: the whole chain
```

Per-step resources are in `run_files/resources.cfg` (time, CPUs, memory, `batch` = array tasks at
once). Edit it there and re-run `jobs` before `submit`. To submit only some steps:
`topsstack.py submit ChileSenAT120.txt -s 13 -e 16` (or `-l 13 15`).

### 7. While it runs

```bash
squeue -u $USER
topsstack.py status ChileSenAT120.txt 13-16             # bad rows per step (missing / wrong-size outputs)
topsstack.py status ChileSenAT120.txt 15 --rerun --submit   # rerun only the bad rows, re-link the chain
```

With `hpc.gate = yes` you get no mail per step. The gate reruns bad rows itself (up to
`hpc.gateRetries`), then either lets the next step start or stops the chain and mails you the bad
rows with their log lines. At the end you get one mail with the summary of all steps.

### 7b. Ionosphere check, then steps 24-28 (ionosphere only)

```bash
topsstack.py ionqc ChileSenAT120.txt --raw --unw        # ~1 min -> logs/ionqc_<date>.txt/.csv
topsstack.py plot  ChileSenAT120.txt                    # ion figures, flagged pairs boxed (below)
topsstack.py ionqc ChileSenAT120.txt --raw --unw --apply   # add "exclude" pairs to --exc_pair of steps 24/26
topsstack.py submit ChileSenAT120.txt -s 24
```

`ionqc` excludes a pair when its raw ionosphere has blocks (unwrapping errors) or it fails loop
closure, and the correction does not help its interferogram; the network stays connected. Lists:
`logs/ionqc_exclude.txt`, `logs/ionqc_check.txt` (look at these). Details: `commands/ionqc.py`.
Optional `--network`: a second opinion from the residual of each pair against the whole network
(iteratively reweighted least squares). Pairs it down-weights (weight < 0.5 and residual > 0.3 x their
own signal) are added to "look at" only, never excluded; CSV columns `irls_*`. Not the default: on the
Chile tracks it improved the held-out prediction by a median 2 % (asc) / 0 % (dsc) over the curated lists.

### 8. Free disk space (optional, any time)

```bash
topsstack.py clean ChileSenAT120.txt                    # table: what may be deleted after which step
topsstack.py clean ChileSenAT120.txt esd coreg_overlap  # dry run: files and sizes
topsstack.py clean ChileSenAT120.txt esd coreg_overlap --delete --reuse
```

`--delete` refuses a file type while its last reader (the kill-after step) still has bad or
unfinished rows. The SLC zips are read until step 17 with ionosphere (step 13 without).

### 9. After the last step

```bash
topsstack.py plot   ChileSenAT120.txt                   # = plot ... ion: all ionosphere figures
topsstack.py report ChileSenAT120.txt                   # time, CPU, memory, cost, size per step
topsstack.py export ChileSenAT120.txt --dry-run         # then without --dry-run (needs hpc.exportDir)
```

## Figures (`topsstack.py plot TEMPLATE [ion|unw|baselines|network]`)

Open `pic/<name>/collage.html` in a browser. Images are skipped if their PNG exists; add `--redo` to
redraw (other options after the target go to `scripts/plot_imgs.py`, e.g. `-n 16` threads).

| target | `pic/` | content | colorbar cycle |
|---|---|---|---|
| `ion` (default) | `img_ion_amp` | ionosphere per pair, `ion/*/ion_cal/filt.ion` | 2π rad |
| | `img_ion_dates_amp` | ionosphere per date (step 24) | 2π rad |
| | `img_azshiftDate_amp` | azimuth shift per date (step 26) | 0.00628 single-look lines (~9 cm) |
| | `img_ionRampDate_amp` | burst phase ramp per date (step 28) | 0.0628 rad |
| `unw` | `img_unw_amp` | all interferograms (many; only on request) | 2π rad |
| `baselines` | `pic/` | perpendicular-baseline history | |
| `network` | `pic/` | networks by starting range / IPF version | |

Per-date figures (`--diff`): each date minus the previous one, then the last date (cumulative) and the
linear rate per pixel [unit/yr]; over an interferogram amplitude. Boxes on the pair figures: blue = `pairs_diff_starting_ranges.txt`, orange = ionqc check,
red = ionqc exclude (after `ionqc`). Own lists: `plot TEMPLATE ion --mark FILE:COLOR:LABEL`.

## The template

`isce.*` keys go to `stackSentinel.py` through `run_isce_stack.py` (see `run_isce_stack.py -h` and
`utils/config.py` for all keys and defaults). The others are read by `topsstack.py` only:

| group | keys (all optional) | derived when `auto` |
|---|---|---|
| `asf.*` | `dataDir wkt bbox relativeOrbit flightDirection platforms start end processes shards` | orbit and direction from the name (`...SenAT076`), AOI from `isce.boundingBox`, data in `../data` |
| `dem.*` | `dir snwe buffer waterBody` | integer box = bounding box + 1° |
| `select.*` | `southNorth minAcq numConnections bridge` | S/N from the bounding box |
| `ion.*` | `wbdFile maskFile iteration fill swathAlign burstRampMask` | water body next to `isce.demFile`, same box; 5 iterations; fill nearest; no swath_align |
| `hpc.*` | `track account mail ompTopo clean gate gateRetries gateSteps diskSteps gpuType exportDir costPerCpuHour gpuUnits` | `a076`, `simonsgroup`, `$USER@caltech.edu`, gate on, 2 retries, gates after 1,5,7,9,13,15-20,24,27 + the last, v100, $0.012/unit, 10 units/GPU h |

## How the chain is protected

- **Exit status:** each task exits with its command's status, so a failed command is `FAILED` in Slurm
  and the `afterok` chain stops. `COMPLETED` is still not proof of outputs; `status` is.
- **`status`** reads each row's outputs from its config and checks existence and size against the
  `.xml`. A missing binary whose `.xml` is still there is reported as `cleaned`, not failed.
- **Gate (`hpc.gate = yes`):** after each step, a small job runs `status --gate`: it reruns bad rows up
  to `hpc.gateRetries` times and lets the next step start only when every row is good (else the chain
  stops and you get a mail). Parts of a step then chain with `afterany`.
- **`clean --delete`** deletes a file type only if its last reader (the kill-after step in the
  `clean` table) has all its outputs. The table is derived from the stack: bursts read from the SLC zips
  through VRTs (always, in topsStack), `virtual_merge`, and whether ionosphere steps exist.
- **Disk use** is recorded after each step by `disk_usage.job`, outside the chain.
- `hpc.clean = yes` activates the deletion lines written into the job files (commented otherwise).

## Ionosphere (steps 17–28)

- 17–24: ionospheric phase from range sub-bands ([Liang et al., 2019](https://ieeexplore.ieee.org/document/8706258)).
  Step 17 reads the SLC zips again: keep them until it has finished.
- 22: pairs with different swath starting ranges (`pairs_diff_starting_ranges.txt`) are unwrapped
  and computed per swath (6 snaphu runs per pair), then `mergeSwathIon.py` aligns the swaths by one
  constant each. `ion.swathAlign = yes` adds a second alignment in filtIon; default no (a076: worse).
- 23 (`filtIon.py`) masks: coherence > 0.75, height < 5000 m, common sub-band connected component,
  water body (`wbdfile`) and `maskfile` if set (`stack --ion-config` sets them).
- 24/26 (`invertIon.py`) invert pairs to dates; they stop if the network is not connected.
- 25–28: burst azimuth ramps from the ionospheric azimuth shift (PR #600); need ESD applied.
- Mask unwrapping errors before filtering: `ion.maskFile` (e.g. `otsu_masking.py`), `stack
  --ion-config`, re-run from 23.

## Notes

- `submit` calls `sbatch` two or three times per step (step, gate, disk usage); on a busy controller
  that can take ~30 s per call, hence `nohup ... &`.
- GPU steps request `--gres=gpu:<hpc.gpuType>:<n>` (Slurm here rejects a count without a type);
  with `isce.useGPU = no` no GPU is requested at all.
- `run_01_unpack_topo_reference` runs a Python pool of `Ncpus_per_task / hpc.ompTopo` processes
  (one per burst is fastest), each with `hpc.ompTopo` OpenMP threads, on one node (≤ 56 CPUs here).
- A low fairshare (after heavy use) can leave only a few array tasks running at once; that is the
  queue, not an error (`squeue` reason `Priority`).
- The previous scripts are in `scripts/obsolete/`, with the old README (`README_v1.md`).
