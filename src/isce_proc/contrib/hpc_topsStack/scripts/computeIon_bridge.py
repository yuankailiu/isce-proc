#!/usr/bin/env python3
"""computeIon.py with a step-robust removal of the relative sub-band unwrapping errors.

topsStack computeIon.py (adjFlag=1, isceobj/TopsProc/runIon.py computeIonosphere) fits a 2nd-order polynomial to
L-U (cor >= 0.85) and rounds (L-U - fit)/2pi per pixel. A polynomial cannot follow a step: when one sub-band slips
by one cycle across a narrow land bridge (a160: Suez isthmus), the whole block keeps a one-cycle relative error,
i.e. ~638 rad of ionosphere (fl*fu^2*2pi / (f0*(fu^2-fl^2))).

Here: c = median(L-U) over the valid cor >= cor_th pixels; per-pixel cycle n = round((L-U)/2pi - c/2pi) from the
cor >= cor_th pixels only; n is then replaced by the majority value in a win x win window (high-coherence votes),
so noisy pixels follow their neighbours; pixels without any vote in the window keep their own rounding.
upper += n*2pi, then the dispersive formula of computeIonosphere. Same inputs/outputs as computeIon.py.
"""
import argparse
import os
import sys

import numpy as np
from scipy.ndimage import uniform_filter

import isceobj
from isceobj.Constants import SPEED_OF_LIGHT


def cmd():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('-l', '--lower', required=True, help='lower band unwrapped interferogram (fine_look.unw)')
    p.add_argument('-u', '--upper', required=True, help='upper band unwrapped interferogram (fine_look.unw)')
    p.add_argument('-c', '--coherence', required=True, help='coherence (fine_look.cor)')
    p.add_argument('-i', '--ionosphere', required=True, help='output ionosphere (raw_no_projection.ion)')
    p.add_argument('-o', '--coherence_output', required=True, help='output coherence (raw_no_projection.cor)')
    p.add_argument('--cor_th', type=float, default=0.85, help='coherence of the pixels that vote (default: 0.85)')
    p.add_argument('--win', type=int, default=9, help='majority window [ion-grid pixels] (default: 9)')
    return p.parse_args()


def bridge(lower, upper, cor, cor_th=0.85, win=9):
    """Integer cycles n (per pixel) that make upper consistent with lower; returns n and a small report."""
    valid = (lower != 0) & (upper != 0)
    dd = (lower - upper) / (2 * np.pi)
    good = valid & (cor >= cor_th)
    if good.sum() < 100:                                  # too few votes: one constant only
        good = valid
    c = np.median(dd[good])
    n_pix = np.where(valid, np.around(dd - c), 0)
    vals = np.unique(n_pix[good])
    votes = np.stack([uniform_filter(((n_pix == v) & good).astype(np.float32), win, mode='nearest') for v in vals])
    n = vals[np.argmax(votes, axis=0)]
    novote = votes.max(axis=0) == 0
    n[novote] = n_pix[novote]
    n[~valid] = 0
    rep = {'c_cycles': c, 'cycles': {int(v): int(((n == v) & valid).sum()) for v in np.unique(n[valid])},
           'pix_changed_by_majority': int((valid & (n != n_pix)).sum()), 'no_vote': int((valid & novote).sum())}
    return n, rep


def main():
    inps = cmd()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from shutil import which
    sys.path.insert(0, os.path.dirname(os.path.realpath(which('computeIon.py'))))
    from computeIon import ionParam                      # sub-band wavelengths, as computeIon.py

    img = isceobj.createImage()
    img.load(inps.lower + '.xml')
    width, length = img.width, img.length
    rd = lambda f, b: np.fromfile(f, dtype=np.float32).reshape(length * 2, width)[b:length * 2:2, :]
    lower, upper, cor, amp = rd(inps.lower, 1), rd(inps.upper, 1).copy(), rd(inps.coherence, 1), rd(inps.coherence, 0)

    ip = ionParam()
    ip.configure()
    fl = SPEED_OF_LIGHT / ip.radarWavelengthLower
    fu = SPEED_OF_LIGHT / ip.radarWavelengthUpper
    f0 = (fl + fu) / 2.0

    n, rep = bridge(lower, upper, cor, inps.cor_th, inps.win)
    print(f'relative sub-band cycles: constant {rep["c_cycles"]:+.3f}; n (pixels) {rep["cycles"]}; '
          f'{rep["pix_changed_by_majority"]} pixels set by the {inps.win}x{inps.win} majority; {rep["no_vote"]} without vote')
    upper += n * 2 * np.pi
    ionos = fl * fu * (lower * fu - upper * fl) / f0 / (fu ** 2 - fl ** 2)
    ionos[(lower == 0) | (rd(inps.upper, 1) == 0)] = 0

    for fname, band2 in ((inps.ionosphere, ionos), (inps.coherence_output, cor)):
        os.makedirs(os.path.dirname(os.path.abspath(fname)), exist_ok=True)
        out = np.zeros((length * 2, width), dtype=np.float32)
        out[0:length * 2:2, :] = amp
        out[1:length * 2:2, :] = band2
        out.tofile(fname)
        img.filename = fname
        img.extraFilename = fname + '.vrt'
        img.renderHdr()


if __name__ == '__main__':
    main()
