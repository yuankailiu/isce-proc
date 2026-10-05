#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Unified GAM correction for ISCE2 or MintPy interferograms
#
# Yuan-Kai Liu
# Copyright 2024-2025, Caltech
#
# 2026-10-01: default unwrapper is whirlwind (whirlwind-insar, MCF; in memory, stack coherence, connected
#   components written to connectComponent). --unwrapper snaphu keeps the ISCE2 path, now with full
#   statistical-cost optimisation and NCORRLOOKS from --nlooks (geocoded stacks carry RLOOKS = ALOOKS = 1,
#   which made NCORRLOOKS 0.6 and the SNAPHU cost model near-uniform). Old behaviour:
#   --unwrapper snaphu --snaphu-init-only --nlooks 0.6. Tests: chile/andean-crust/UNWRAP_A018_FULLSPAN.md.

import argparse
import glob
import multiprocessing as mp
import os
import sys
from multiprocessing import shared_memory

import numpy as np
from mintpy.objects import ifgramStack
from mintpy.utils import isce_utils, readfile
from mintpy.utils import utils as ut
from mintpy.utils import writefile
from tqdm import tqdm

#####################################################
#   ISCE2 writer helper
#####################################################

def write_binary2isce2(data, out_file, meta):
    """Write numpy array to ISCE2-style binary + metadata (ROI_PAC-compatible)."""
    # enforce dtype and memory order
    data_cpx = np.asarray(data, dtype=np.complex64, order="C")

    # write raw binary
    with open(out_file, "wb") as fid:
        data_cpx.ravel(order="C").tofile(fid)

    # update meta so WIDTH/LENGTH match the array
    meta['FILE_TYPE']  = '.int'
    meta['INTERLEAVE'] = 'BIP'
    meta['DATA_TYPE']  = 'complex64'
    meta['BANDS']      = 1
    meta['WIDTH']      = data.shape[1]
    meta['LENGTH']     = data.shape[0]
    meta['width']      = meta['WIDTH']
    meta['length']     = meta['LENGTH']
    for k, v in meta.items():
        if k.isupper() and k not in meta: meta[k]=v
    writefile.write_isce_xml(meta, out_file, print_msg=False)
    writefile.write_roipac_rsc(meta, out_file + '.rsc', print_msg=False)
    return out_file


def load_ifgs(int_input):
    """
    Load interferograms from MintPy ifgramStack.h5 or ISCE2-style .int files.
    Returns
           list of phase values   [float32] or
           list of complex values [complex64],
           date12 list, metadata,
           coherence stack [float32] (MintPy input only; None for ISCE2 input)
    """
    coh = None
    if int_input.endswith('.h5'):
        # MintPy ifgramStack
        print(f'Loading MintPy ifgramStack: {int_input}')
        stack_obj = ifgramStack(int_input)
        stack_obj.open(print_msg=False)
        length, width = stack_obj.length, stack_obj.width
        meta = dict(stack_obj.metadata)
        date12_list = stack_obj.get_date12_list(dropIfgram=False)
        data = readfile.read(int_input, datasetName='wrapPhase')[0] # phase [float32]
        coh = readfile.read(int_input, datasetName='coherence')[0]  # spatial coherence [float32]
    else:
        # ISCE2 interferograms folder
        print(f'Reading isce2 interferograms from: {int_input}')
        paths = glob.glob(int_input)
        date12_list = []
        data = []
        for p in tqdm(paths):
            date12 = os.path.basename(os.path.dirname(p))
            _data, meta = readfile.read_binary_file(p, datasetName='complex') # [complex64]
            data.append(_data)
            date12_list.append(date12)

    return data, date12_list, meta, coh



#####################################################
#   GAM reconstruction
#####################################################
def timeseries2ifgram(ts_file, ifgram_file, out_file='GAM_Ifg.h5', max_memory=4):
    """Reconstruct interferograms from timeseries, MintPy-style (always box-by-box).

    Parameters
    ----------
    ts_file     : str, path to timeseries.h5
    ifgram_file : str, path to reference ifgramStack.h5
    out_file    : str, output file name
    max_memory  : float, maximum memory usage per block in GB
    """
    # read attributes
    atr = readfile.read_attribute(ts_file)
    length, width = int(atr['LENGTH']), int(atr['WIDTH'])
    range2phase = -4.*np.pi / float(atr['WAVELENGTH'])

    # get time dimension info
    ts_shape = readfile.read(ts_file, datasetName='timeseries')[0].shape
    num_date = ts_shape[0]

    # design matrix
    stack_obj = ifgramStack(ifgram_file)
    stack_obj.open(print_msg=False)
    date12_list = stack_obj.get_date12_list(dropIfgram=False)
    A = stack_obj.get_design_matrix4timeseries(date12_list, refDate='no')[0]
    num_ifg = A.shape[0]

    # prepare output layout

    # split date12_list into start/end dates
    dates = np.array([d.split('_') for d in date12_list], dtype='S8')  # shape (num_ifg, 2)

    ds_dict = {
        'bperp'      : [np.float32 , (num_ifg,)  , stack_obj.pbaseIfgram],
        'date'       : [dates.dtype, (num_ifg, 2), dates],
        'dropIfgram' : [np.int8    , (num_ifg,)  , stack_obj.dropIfgram.astype(np.int8)],
        'unwrapPhase': [np.float32 , (num_ifg    , length, width), None],
    }

    writefile.layout_hdf5(out_file, ds_dict, metadata=atr, ref_file=ifgram_file)

    # split into boxes
    box_list, num_box = stack_obj.split2boxes(
        max_memory=max_memory,
        dim0_size=num_date + num_ifg
    )

    for i, box in enumerate(box_list):
        print(f'Processing timeseries patch {i+1}/{num_box}, box={box}')
        # MintPy: box = [x0, x1, y0, y1]
        x0, x1 = box[0], box[2]
        y0, y1 = box[1], box[3]
        box_wid = x1 - x0
        box_len = y1 - y0
        if box_wid <= 0 or box_len <= 0:
            print(f"[Warning] Skip empty/invalid box {box}")
            continue

        # read a box of timeseries
        ts_data = readfile.read(ts_file, datasetName='timeseries', box=box)[0] * range2phase
        ts_data = ts_data.reshape(num_date, -1)  # (num_date, n_pix)

        # reconstruct ifgram for this box
        ifgram_est_box = np.dot(A, ts_data).reshape(num_ifg, box_len, box_wid)

        # write block
        block = [0, num_ifg, y0, y1, x0, x1]
        writefile.write_hdf5_block(
            out_file,
            data=ifgram_est_box.astype(np.float32),
            datasetName='unwrapPhase',
            block=block
        )

    print(f"Saved reconstructed GAM interferograms: {out_file}")
    return out_file


#####################################################
#   Core correction + unwrap
#####################################################
def unwrap_it(int_file, cor_file, unw_file, overwrite=False, init_only=False):
    """Single interferogram unwrap using ISCE2 helpers (snaphu).
    NCORRLOOKS is read by unwrap_snaphu from the .int metadata (set from --nlooks)."""
    if os.path.isfile(unw_file) and not overwrite:
        return unw_file
    isce_utils.estimate_coherence(int_file, cor_file)
    isce_utils.unwrap_snaphu(int_file, cor_file, unw_file, init_only=init_only)
    return unw_file


def unwrap_whirlwind(cpx, coh, valid, nlooks=20.0):
    """Single interferogram unwrap with whirlwind (MCF, SNAPHU-like cost model and components).
    cpx: complex64 phasor with no-data = 0; coh: spatial coherence; valid: bool mask.
    Returns unwrapped phase (0 at no-data) [float32] and connected components (0 = background) [int16]."""
    import whirlwind as ww
    c = np.nan_to_num(np.where(valid, coh, 0)).astype(np.float32)
    unw, cc = ww.unwrap(np.asarray(cpx, dtype=np.complex64), c, max(float(nlooks), 1.0), mask=valid)
    unw = np.where(valid, np.nan_to_num(unw), 0).astype(np.float32)
    cc = np.where(valid, cc, 0).astype(np.int16)
    return unw, cc


def remove_tropo_unwrap(data_list, gam_list, date12_list, meta, out_dir,
                        filter_type='goldstein', params=(0.3,),
                        mask=None, src_tag='_GAM',
                        unwrap=True, overwrite=False, add_back=False, nproc=4,
                        unwrapper='whirlwind', nlooks=20.0, coh_list=None, snaphu_init_only=False):
    """
    Remove tropospheric phase screen (GAM) from wrapped interferograms,
    filter, and unwrap (MintPy-style parallel driver).

    unwrapper : 'whirlwind' (default; in memory, uses coh_list or, if None, the ICU phase-sigma
                coherence of the written .int) or 'snaphu' (ISCE2 snaphu, SMOOTH cost).
    nlooks    : effective looks of the coherence (whirlwind nlooks / snaphu NCORRLOOKS).
    Returns (unw_list, cor_list, cc_list); cor_list/cc_list are None for snaphu (read from files).
    """
    os.makedirs(out_dir, exist_ok=True)
    if unwrapper == 'snaphu':
        meta = dict(meta); meta['NCORRLOOKS'] = str(nlooks)     # read back by isce_utils.unwrap_snaphu
        print(f'unwrapper: snaphu (init_only={snaphu_init_only}, NCORRLOOKS={nlooks})')
    else:
        print(f'unwrapper: whirlwind (nlooks={nlooks}, coherence: '
              f'{"input stack" if coh_list is not None else "ICU phase sigma"})')

    if unwrapper == 'whirlwind':
        if not unwrap:
            raise ValueError('unwrap=False is only supported with unwrapper="snaphu" (writes .int files)')
        import whirlwind as ww
        try:
            ww.set_num_threads(nproc)
        except RuntimeError:                                   # thread pool already initialised
            pass
        print("Step 1+2: correct (subtract GAM), filter and unwrap with whirlwind ...")
    else:
        print("Step 1: correct (subtract GAM), filter, and write .int ...")
    int_files, cor_files, unw_files = [], [], []
    ww_files = []     # whirlwind: (per-pair .npz, unwrapped in this run)
    valid_list = []   # per-interferogram no-data mask, re-applied after unwrapping

    # filter (gaussian OR goldstein) -> write final .int
    if filter_type == 'gaussian':
        sx, sy, sigx, sigy = params
        print('-'*80)
        print(f'filter the wrapped phase stack with a Gaussian kernel of {sx} x {sy}, sigma of {sigx} x {sigy} ...')
        kernel = isce_utils.gaussian_kernel(sx, sy, sigx, sigy)
    elif filter_type == 'goldstein':
        print('-'*80)
        print(f'filter the wrapped phase stack with a Goldstein filter of strength {params[0]} ...')
    else:
        print('no filter is applied before unwrapping')
    print(f'number of wrapped phase: {len(data_list)}')


    for i, (ifg, gam, date12) in enumerate(tqdm(zip(data_list, gam_list, date12_list))):

        valid = (ifg!=0) & ~np.isnan(ifg)
        if mask is not None:
            valid &= mask

        if ifg.dtype == np.float32:
            # complex phasor, note the amplitude is arbitrary 1
            ifgcpx = np.exp(1j * ifg)
        else:
            ifgcpx = np.array(ifg)

        # subtract GAM in complex domain
        cpx = ifgcpx * np.exp(-1j * gam)

        cpx[~valid] = 0+0j
        valid_list.append(valid)

        if unwrapper == 'whirlwind':
            # in memory, one interferogram at a time; resumable through the per-pair .npz
            ww_file = os.path.join(out_dir, f'{date12}{src_tag}_ww.npz')
            fresh = overwrite or not os.path.isfile(ww_file)
            if fresh:
                if filter_type == 'gaussian':
                    cpx = isce_utils.convolve(data=cpx, kernel=kernel).astype(np.complex64)
                    cpx[~valid] = 0
                elif filter_type == 'goldstein':
                    import whirlwind as ww
                    cpx = ww.goldstein(np.asarray(cpx, dtype=np.complex64), alpha=params[0]).astype(np.complex64)
                    cpx[~valid] = 0
                if coh_list is not None:
                    coh = coh_list[i]
                else:                                          # ICU phase sigma of the (filtered) phasor
                    int_file = os.path.join(out_dir, f'{date12}{src_tag}.int')
                    cor_file = int_file.replace('.int', '.cor')
                    write_binary2isce2(cpx.astype(np.complex64), int_file, meta)
                    isce_utils.estimate_coherence(int_file, cor_file)
                    coh = readfile.read(cor_file)[0]
                unw, cc = unwrap_whirlwind(cpx, coh, valid, nlooks)
                np.savez(ww_file + '.tmp.npz', unw=unw, cc=cc, coh=np.asarray(coh, dtype=np.float32))
                os.replace(ww_file + '.tmp.npz', ww_file)
            ww_files.append((ww_file, fresh))
            continue

        # output paths
        int_file = os.path.join(out_dir, f'{date12}{src_tag}.int')
        cor_file = int_file.replace('.int', '.cor')
        unw_file = int_file.replace('.int', '.unw')

        # filter (gaussian OR goldstein) -> write final .int
        if filter_type == 'gaussian':
            cpx_f = isce_utils.convolve(data=cpx, kernel=kernel).astype(np.complex64)
            write_binary2isce2(cpx_f, int_file, meta)
        elif filter_type == 'goldstein':
            tmp = int_file.replace('.int', '_tmp.int')
            write_binary2isce2(cpx.astype(np.complex64), tmp, meta)
            isce_utils.filter_goldstein(tmp, int_file, filt_strength=params[0])
            for f in glob.glob(tmp + '*'):
                os.remove(f)
        else:
            write_binary2isce2(cpx.astype(np.complex64), int_file, meta)

        int_files.append(int_file)
        cor_files.append(cor_file)
        unw_files.append(unw_file)


    if unwrapper == 'whirlwind':
        unw_list, cor_list, cc_list = [], [], []
        for i, (ww_file, fresh) in enumerate(ww_files):
            z = np.load(ww_file)
            unw = z['unw']
            # the .npz holds the phase without GAM; add it back on every read (the .npz is never modified)
            if add_back:
                unw = unw + gam_list[i]
            unw = np.where(valid_list[i], unw, 0).astype(np.float32)
            unw_list.append(unw); cor_list.append(z['coh']); cc_list.append(z['cc'])
        print(f'whirlwind: {sum(f for _, f in ww_files)} unwrapped in this run, '
              f'{sum(not f for _, f in ww_files)} reused from {out_dir}')
        return unw_list, cor_list, cc_list

    if not unwrap:
        return None

    n = len(int_files)
    # files unwrapped in THIS run; existing .unw already carry the added-back GAM
    fresh = [overwrite or not os.path.isfile(u) for u in unw_files]
    print(f"Step 2: unwrapping {n} interferograms (MintPy-style driver, nproc={nproc}) ...")
    num_core, run_parallel, Parallel, delayed = ut.check_parallel(
        n, print_msg=False, maxParallelNum=nproc
    )

    if run_parallel and num_core > 1:
        print(f'parallel processing using {num_core} cores')
        _ = Parallel(n_jobs=num_core)(
                delayed(unwrap_it)(i_f, c_f, u_f, overwrite, snaphu_init_only)
                for i_f, c_f, u_f in zip(int_files, cor_files, unw_files)
            )
    else:
        print('serial processing ...')
        for i_f, c_f, u_f in zip(int_files, cor_files, unw_files):
            unwrap_it(i_f, c_f, u_f, overwrite, snaphu_init_only)

    unw_list = []
    for i, unw_file in enumerate(tqdm(unw_files)):
        unw, meta = readfile.read_binary_file(unw_file, datasetName='phase')  # float32 unwrapped

        # snaphu returns values at no-data pixels too: reset them with THIS interferogram's mask
        # (previously the mask of the last interferogram of the Step-1 loop was used for all)
        if add_back and fresh[i]:
            print(f"Adding GAM back to unwrapped phase {i+1}/{len(unw_files)} ...")
            unw = unw + gam_list[i]   # add tropo back (radians)
        unw[~valid_list[i]] = 0.0
        writefile.write(unw.astype(np.float32), unw_file, meta)

        unw_list.append(unw)

    return unw_list, None, None




#####################################################
#   Main
#####################################################

def parse_args():
    parser = argparse.ArgumentParser(
        description="Unified GAM (tropospheric correction) tool for ISCE2 or MintPy interferograms",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "-g", "--gam", required=True,
        help="Path to GAM correction file (e.g. ERA5.h5 or ERA5_Ifg.h5). "
             "If a timeseries file is given, will reconstruct or use *_Ifg.h5 if available."
    )
    parser.add_argument(
        "-f", "--ifg", required=True,
        help="Path to ifgramStack file (MintPy) or interferogram directory (ISCE2)."
    )
    parser.add_argument(
        "-i", "--int", default=None,
        help="Optional: Input interferogram file(s). "
             "Default = same as --ifg if it ends with .h5."
    )
    parser.add_argument(
        "-a", "--add", action="store_true",
        help="Add GAM correction back after filtering (useful for partial correction tests)."
    )
    parser.add_argument(
        "-o", "--out", default=None,
        help="Output directory. Default = auto-generated based on input."
    )
    parser.add_argument(
        "--filter", default="no", choices=["goldstein", "gaussian", "no"],
        help="Filtering method to apply before correction."
    )
    parser.add_argument(
        "--gw_alpha", type=float, default=0.3,
        help="Alpha parameter for Goldstein filter (if selected)."
    )
    parser.add_argument(
        "--gauss", nargs=4, type=int, default=[5, 5, 1, 1],
        metavar=("XWIN", "YWIN", "XSIG", "YSIG"),
        help="Gaussian filter parameters: window_x window_y sigma_x sigma_y."
    )
    parser.add_argument(
        "--nproc", type=int, default=4,
        help="Number of processes for parallel execution."
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing outputs if present."
    )
    parser.add_argument(
        "--mask", default=None,
        help="Optional mask file to exclude certain pixels (e.g. water)."
    )
    parser.add_argument(
        "--unwrapper", default="whirlwind", choices=["whirlwind", "snaphu"],
        help="Phase unwrapper. whirlwind: in memory, coherence from the input stack, connected components "
             "from whirlwind; snaphu: ISCE2 snaphu (SMOOTH), coherence re-estimated (ICU phase sigma)."
    )
    parser.add_argument(
        "--nlooks", type=float, default=20.0,
        help="Effective number of looks of the coherence (whirlwind nlooks / snaphu NCORRLOOKS). "
             "Geocoded MintPy stacks carry RLOOKS = ALOOKS = 1, so do not derive it from the metadata."
    )
    parser.add_argument(
        "--snaphu-init-only", action="store_true",
        help="snaphu: MCF initialisation only (the pre-2026-10 behaviour); default is full optimisation."
    )
    parser.add_argument(
        "--outfile", default=None,
        help="Output MintPy stack (MintPy input only). Default: ifgramStack_<GAM>unw.h5 next to the input stack."
    )

    args = parser.parse_args()

    # Extra error checks
    if not args.gam.endswith(".h5"):
        parser.error("The --gam file must be an HDF5 (.h5) file.")
    if not args.ifg:
        parser.error("The --ifg file must be specified.")

    return args



def main():
    args = parse_args()

    #-------------------------------------------
    # reconstruct GAM if needed
    atr_gam = readfile.read_attribute(args.gam)
    src = os.path.splitext(os.path.basename(args.gam))[0].split('_')[0]

    if src.upper().startswith('ERA'):
        src = 'ERA5'
    elif src.upper().startswith('GACOS'):
        src = 'GACOS'
    elif src.upper().startswith('TROP'):
        src = 'tropHgt'

    # candidate Ifg filename
    gam_ifg = os.path.join(os.path.dirname(args.gam), f"{src}_Ifg.h5")

    if atr_gam['FILE_TYPE'] == 'timeseries':
        if os.path.isfile(gam_ifg):
            print(f"Found existing {gam_ifg}, using it instead of reconstructing...")
        else:
            gam_ifg = timeseries2ifgram(args.gam, args.ifg, out_file=gam_ifg)
    elif atr_gam['FILE_TYPE'] == 'ifgramStack':
        gam_ifg = args.gam
    else:
        raise ValueError(f"Unknown FILE_TYPE: {atr_gam['FILE_TYPE']}")
    #-------------------------------------------


    # build ifgs
    if args.int is None:
        if args.ifg.endswith('.h5'):
            args.int = args.ifg
    data, date12_list, meta, coh_stack = load_ifgs(args.int)

    # mask
    mask = readfile.read(args.mask)[0].astype(bool) if args.mask else None
    src_tag = f'_{src}'

    # outdir
    if args.int.endswith('.h5'):
        out_dir = args.out or os.path.join(os.path.dirname(args.int), 'GAMCorrect')
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = args.out or os.path.dirname(os.path.commonprefix([os.path.abspath(p) for p in glob.glob(args.int)]))

    # --- Load GAM (float32) once ---
    gams, _ = readfile.read(gam_ifg, datasetName='unwrapPhase')
    #gams = np.asarray(gams, dtype=np.float32)
    print(f"Loaded GAM: shape={gams.shape}, dtype={gams.dtype}")


    # run
    unw_list, cor_list, cc_list = remove_tropo_unwrap(
        data, gams, date12_list, meta, out_dir, filter_type=args.filter,
        params=(args.gw_alpha,) if args.filter=='goldstein' else tuple(args.gauss),
        mask=mask, src_tag=src_tag,
        unwrap=True, overwrite=args.overwrite,
        add_back=args.add, nproc=args.nproc,
        unwrapper=args.unwrapper, nlooks=args.nlooks, coh_list=coh_stack,
        snaphu_init_only=args.snaphu_init_only)


    # if MintPy input, write back stack
    if args.int.endswith('.h5') and unw_list is not None:
        input_dir  = os.path.dirname(os.path.abspath(args.int))

        if args.unwrapper == 'whirlwind':
            corStack  = np.array(cor_list, dtype=np.float32)
            connStack = np.array(cc_list, dtype=np.int16)
        else:
            corStack  = np.array(
                [readfile.read(os.path.join(out_dir, f'{d12}{src_tag}.cor'))[0] for d12 in date12_list],
                dtype=np.float32
            )
            connStack = np.array(
                [readfile.read(os.path.join(out_dir, f'{d12}{src_tag}.unw.conncomp'))[0] for d12 in date12_list],
                dtype=np.int16
            )
        unwStack  = np.array(unw_list, dtype=np.float32)

        ds_dict = {
            'coherence':        corStack,
            'unwrapPhase':      unwStack,
            'connectComponent': connStack,
        }

        out_file  = args.outfile or os.path.join(input_dir, f'ifgramStack_{src}unw.h5')
        writefile.write(ds_dict, out_file=out_file, ref_file=args.int)
        import h5py
        with h5py.File(out_file, 'r+') as f:                   # provenance of the unwrapping
            f.attrs['UNWRAPPER'] = args.unwrapper
            f.attrs['UNWRAP_NLOOKS'] = str(args.nlooks)
            if args.unwrapper == 'snaphu':
                f.attrs['SNAPHU_INIT_ONLY'] = str(args.snaphu_init_only)
            else:
                import whirlwind as ww
                f.attrs['WHIRLWIND_VERSION'] = ww.version('whirlwind-insar')
        print(f"Saved MintPy corrected stack: {out_file}  (unwrapper: {args.unwrapper})")

    print("Done.")


if __name__ == '__main__':
    main()
