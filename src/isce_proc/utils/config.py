#!/usr/bin/env python3
# Author: Zhang Yunjun, Apr 2023
# Recommend usage:
#   from isce_proc.utils import config


EXAMPLE_TEMPLATE = """template:
##------------------------------- ISCE tops/stripmapStack ------------------------------##
isce.processor          = stripmapStack              #[stripmapStack, topsStack], auto for topsStack
isce.workflow           = interferogram              #[slc / correlation / interferogram / offset], auto for interferogram
isce.demSNWE            = 31.1, 32.8, 130.1, 131.9   #[S, N, W, E] in degree, auto for none (expaned from boundingBox; otherwise error)
isce.demFile            = ./DEM/gsi10m.dem.wgs84     #DEM file name, auto for none (generate on the fly)
isce.demSource          = srtm1                      #[srtm1, srtm3, nasadem, gsi_dehm], auto for srtm1
isce.demFillValue       = 0                          #[0 / 1 / -32768], value used to fill missing DEMs, auto for -32768
isce.demUrl             = none                       #[none, https://e4ftl01.cr.usgs.gov/DP133/SRTM/SRTMGL1.003/2000.02.11], auto for none
isce.demBuffer          = 3                          #[int], buffer btw. SNWE and download DEM tiles, auto for 3
isce.boundingBox        = none                       #[S, N, W, E] in degree, auto for none
isce.referenceDate      = none                       #[20150101 / no], auto for none (1st date)
isce.azimuthLooks       = 3                          #[int], auto for 3
isce.rangeLooks         = 9                          #[int], auto for 9
isce.filtStrength       = 0.5                        #[0.0-1.0], auto for 0.5
isce.unwrapMethod       = snaphu                     #[snaphu / icu], auto for snaphu
isce.useGPU             = no                         #[yes / no], auto for no
isce.numProcess         = 4                          #[int>=1], number of processors, auto for 4
isce.polarization       = vv                         #[vv,hh], auto for vv

##----------for topsStack only:
isce.virtualMerge       = no                         #[yes / no], auto for no, use virtual files for the merged SLCs and geometry
isce.coregistration     = geometry                   #[geometry / NESD], auto for geometry
isce.swathNum           = 1,2                        #[1,2,3], auto for '1,2,3'
isce.numConnection      = 5                          #[int>=1], auto for 3
isce.orbitDir           = ~/bak/aux/aux_poeorb/      #Directory with all orbit files
isce.auxDir             = ~/bak/aux/aux_cal/         #Directory with all aux   files
isce.startDate          = none                       #[20140825 / no], auto for none (1st date)
isce.endDate            = none                       #[20190622 / no], auto for none (last date)
isce.numProcess4topo    = auto                       #auto for numProcess/OMP_NUM_THREADS. Max limited by no. of CPUs per node on server
isce.updateMode         = yes                        #[yes / no], auto for yes
## ionospheric phase estimation
## copy $ISCE_STACK/topsStack/ion_param.txt to the local dir to turn ON iono
isce.numConnectionIon   = 3                          #[int>=1], auto for 3
isce.paramIonFile       = ./ion_param.txt            #Ion param file, auto for none (no iono estimation)

##----------for stripmapStack only:
## Sensors with zero doppler SLC: ALOS2
## link: https://github.com/isce-framework/isce2/blob/master/components/isceobj/StripmapProc/Factories.py#L61
isce.zeroDopper         = no                         #[yes / no], use zero doppler geometry for processing, auto for no
isce.focus              = no                         #[yes / no], do focus, auto for yes (for RAW data)
isce.ALOS.fbd2fbs       = yes                        #[yes / no], auto for yes, convert FBD to FBS for ALOS-1
isce.ALOS2.polarization = HH                         #[HH / VV], auto for HH
isce.maxTempBaseline    = 1800                       # auto for 1800 days
isce.maxPerpBaseline    = 1800                       # auto for 1800 meters
isce.applyWaterMask     = yes                        # auto for yes

##------------------------------- topsstack.py (HPC workflow) ---------------------------##
## Optional; read only by topsstack.py, ignored by run_isce_stack.py.
## SLC search/download (asf_search)
asf.dataDir             = ../data                    #where the SLC zips go (SLC/ links here), auto for ../data
asf.bbox                = auto                       #[S, N, W, E], auto for isce.boundingBox
asf.wkt                 = auto                       #WKT polygon, overrides asf.bbox, auto for none
asf.relativeOrbit       = auto                       #[int], auto from the template name (e.g. ...SenAT076 -> 76)
asf.flightDirection     = auto                       #[ASCENDING / DESCENDING], auto from the name (AT / DT)
asf.platforms           = auto                       #[S1A,S1B,S1C], auto for S1A,S1B,S1C
asf.start               = auto                       #[YYYY-MM-DD], auto for 2014-06-01
asf.end                 = auto                       #[YYYY-MM-DD], auto for today
asf.processes           = auto                       #parallel downloads per process/shard, auto for 6
asf.shards              = auto                       #Slurm array tasks for `download --slurm`, auto for 8
## DEM
dem.dir                 = auto                       #output dir for dem_1_arcsec/ dem_3_arcsec/ wbd_1_arcsec/, auto for ./DEM
dem.snwe                = auto                       #[S, N, W, E] integer degrees, auto for isce.boundingBox + dem.buffer
dem.buffer              = auto                       #[deg], auto for 1
dem.waterBody           = auto                       #[yes / no], also make the 1-arcsec water body, auto for yes
## SLC selection (topsStack s1_select_ion.py)
select.southNorth       = auto                       #[S, N], auto for S, N of isce.boundingBox
select.minAcq           = auto                       #min no. of acquisitions with the same starting ranges, auto for 10
select.numConnections   = auto                       #`stack --select-pairs`: nearest-neighbour pairs to keep, auto for none (keep all)
select.bridge           = auto                       #`stack --select-pairs`: plus the pair this many dates ahead, auto for none
## ionosphere config edits (`stack --ion-config`; replaces filtIon_config.sh)
ion.wbdFile             = auto                       #water body for filtIon, auto: wbd_1_arcsec/ next to the DEM, same box
ion.maskFile            = auto                       #extra mask for filtIon (e.g. otsu_msk.rdr), auto for none
ion.iteration           = auto                       #filtIon iterations, auto for 5
ion.fill                = auto                       #filtIon fill method, auto for nearest
ion.swathAlign          = auto                       #[yes / no] swath_align for pairs in pairs_diff_starting_ranges.txt, auto for yes
ion.burstRampMask       = auto                       #maskfile for burstRampIon, auto for merged/geom_reference/waterBody.rdr
## Slurm
hpc.track               = auto                       #job name tag, auto from the template name (e.g. a076)
hpc.account             = auto                       #Slurm account, auto for simonsgroup
hpc.mail                = auto                       #mail address, auto for $USER@caltech.edu
hpc.ompTopo             = auto                       #OMP threads per process in run_01, auto for 4
hpc.clean               = auto                       #[yes / no], activate the deletion lines in the jobs, auto for no
hpc.gate                = auto                       #[yes / no], check outputs (and rerun bad rows) between steps, auto for no
hpc.gateRetries         = auto                       #reruns per step by the gate before it stops the chain, auto for 2
hpc.gpuType             = auto                       #GPU type for GPU steps (isce.useGPU = yes), auto for v100
hpc.exportDir           = auto                       #target of `topsstack.py export`, auto for none
hpc.costPerCpuHour      = auto                       #$ per CPU hour for `topsstack.py report`, auto for 0.008
"""


AUTO_DICT = {
    'isce.processor'      : 'topsStack',
    'isce.workflow'       : 'interferogram',
    'isce.demSNWE'        : None,
    'isce.demFile'        : None,
    'isce.demSource'      : 'srtm1',
    'isce.demFillValue'   : '-32768',
    'isce.demUrl'         : None,
    'isce.demBuffer'      : 3,
    'isce.boundingBox'    : None,
    'isce.referenceDate'  : None,
    'isce.azimuthLooks'   : '3',
    'isce.rangeLooks'     : '9',
    'isce.filtStrength'   : '0.5',
    'isce.unwrapMethod'   : 'snaphu',
    'isce.useGPU'         : False,
    'isce.numProcess'     : 4,
    'isce.polarization'   : 'vv',

    #for topsStack only
    'isce.virtualMerge'       : False,
    'isce.coregistration'     : 'geometry',
    'isce.swathNum'           : '1,2,3',
    'isce.numConnection'      : '3',
    'isce.orbitDir'           : '~/bak/aux/aux_poeorb/',
    'isce.auxDir'             : '~/bak/aux/aux_cal/',
    'isce.startDate'          : None,
    'isce.endDate'            : None,
    'isce.numProcess4topo'    : None,
    'isce.updateMode'         : True,
    'isce.numConnectionIon'   : '3',
    'isce.paramIonFile'       : None,

    #for stripmapStack only
    'isce.zeroDopper'         : False,   # key name as spelled in the templates
    'isce.focus'              : True,
    'isce.ALOS.fbd2fbs'       : True,
    'isce.ALOS2.polarization' : 'HH',
    'isce.maxTempBaseline'    : '1800',
    'isce.maxPerpBaseline'    : '1800',
    'isce.applyWaterMask'     : True,

    # topsstack.py (HPC workflow); None/'' = derived at run time, see contrib/hpc_topsStack/commands/config.py
    'asf.dataDir'             : '../data',
    'asf.bbox'                : None,
    'asf.wkt'                 : None,
    'asf.relativeOrbit'       : None,
    'asf.flightDirection'     : None,
    'asf.platforms'           : 'S1A,S1B,S1C',
    'asf.start'               : '2014-06-01',
    'asf.end'                 : None,
    'asf.processes'           : 6,
    'asf.shards'              : 8,
    'dem.dir'                 : './DEM',
    'dem.snwe'                : None,
    'dem.buffer'              : 1,
    'dem.waterBody'           : True,
    'select.southNorth'       : None,
    'select.minAcq'           : 10,
    'select.numConnections'   : None,
    'select.bridge'           : None,
    'ion.wbdFile'             : None,
    'ion.maskFile'            : None,
    'ion.iteration'           : 5,
    'ion.fill'                : 'nearest',
    'ion.swathAlign'          : True,
    'ion.burstRampMask'       : None,
    'hpc.track'               : None,
    'hpc.account'             : 'simonsgroup',
    'hpc.mail'                : None,
    'hpc.ompTopo'             : 4,
    'hpc.clean'               : False,
    'hpc.gate'                : False,
    'hpc.gateRetries'         : 2,
    'hpc.gpuType'             : 'v100',
    'hpc.exportDir'           : None,
    'hpc.costPerCpuHour'      : 0.008,
}
