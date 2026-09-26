#!/usr/bin/env python3
#^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# Author: Cunren Liang,
#         Yuan-Kai Liu,
#         Ollie Stephenson
# Last update: Sept 2025 (YKL)
#^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# refer to:
# https://github.com/isce-framework/isce2/blob/main/contrib/stack/topsStack/plotIonPairs.py

import argparse
import glob
import os
import sys
import numpy as np
import tempfile
import isce, isceobj
from argparse import RawTextHelpFormatter
from isceobj.Alos2Proc.Alos2ProcPublic import runCmd
import shutil

IM = "magick" if shutil.which("magick") else "convert"   # ImageMagick 7 / 6


def cmdLineParse():
    EXAMPLE = """
    plot_imgs.py -i 'ion/*_*/ion_cal/filt.ion'                --redo --loc -3 --band 2 --out pic/img_ion         --amp  --txt pairs.txt
    plot_imgs.py -i 'ion_dates/*.ion'                         --redo --loc  1 --band 1 --out pic/img_ion_dates   --wrap 6.28
    plot_imgs.py -i 'ion_azshift_dates/*.ion'                 --redo --loc  1 --band 1 --out pic/img_azshiftDate --wrap 0.00628
    plot_imgs.py -i 'ion_burst_ramp_merged_dates/*.float'     --redo --loc -1 --band 1 --out pic/img_ionRampDate --wrap 0.0628
    plot_imgs.py -i 'merged/interferograms/*_*/filt_fine.unw' --redo --loc -2 --band 2 --out pic/img_unw
    """

    parser = argparse.ArgumentParser(
        description="Batch plot ISCE rasters with mdx, and optionally collage into SVG/HTML.",
        formatter_class=RawTextHelpFormatter,
        epilog=EXAMPLE,
    )
    parser.add_argument("-i", "--in", dest="input", required=True,
                        help="Glob pattern for files (.int, .unw, .ion, .float, etc.)")
    parser.add_argument("-l", "--loc", dest="loc", type=int, default=-3,
                        help="Index of pair/date substring in path (default: %(default)s)")
    parser.add_argument("-b", "--band", dest="band", type=int, default=2,
                        help="Raster band: 1=amplitude, 2=phase (default: %(default)s)")
    parser.add_argument("-w", "--wrap", dest="wrap", type=float, default=6.28,
                        help="Wrap range (default: %(default)s)")
    parser.add_argument("-o", "--out", dest="outdir", default="./img",
                        help="Output folder (default: %(default)s)")
    parser.add_argument("-r", "--redo", dest="redo", action="store_true",
                        help="Replot all images (default: only those without a .png)")
    parser.add_argument("--diff", dest="diff", action="store_true",
                        help="Per-date files: plot each date minus the previous one, then the last date"
                             " (cumulative) and the linear rate [unit/yr] as the last two panels")
    parser.add_argument("--amp-file", dest="amp_file", default=None,
                        help="Amplitude background for single-band images (band 1 of this ISCE file, same size),"
                             " e.g. merged/interferograms/<pair>/filt_fine.unw for the per-date products")
    parser.add_argument("-a", "--amp", dest="overamp", action="store_true",
                        help="Overlay amplitude (only valid for band=2)")
    parser.add_argument("-m", "--mask", dest="maskfile", default=None,
                        help="Optional mask raster (same size, e.g. waterBody.rdr, 0=water,1=land)")
    parser.add_argument("--mark", dest="mark", action="append", default=[], metavar="FILE[:COLOR[:LABEL]]",
                        help="Box the dates/pairs listed in FILE (repeatable; later ones on top), "
                             "e.g. --mark pairs_diff_starting_ranges.txt:blue:'diff. starting ranges'")
    parser.add_argument("-u", "--unit", dest="unit", default="rad",
                        help="Unit of the colorbar values (default: %(default)s)")
    parser.add_argument("-t", "--txt", dest="date_txt", default=None,
                        help="Text file listing dates/pairs to highlight")
    parser.add_argument("-c", "--collage", dest="collage", action="store_true", default=True,
                        help="Collage the images into collage.html (default: %(default)s)")
    parser.add_argument("--svg", dest="svg", action="store_true",
                        help="Also write collage.svg (links the .png files)")
    parser.add_argument("-n", "--nproc", dest="nproc", type=int, default=8,
                        help="Images rendered in parallel (default: %(default)s)")
    if len(sys.argv) <= 1:
        parser.print_help(); sys.exit(1)
    return parser.parse_args()


if __name__ == "__main__":
    inps = cmdLineParse()

    odir = inps.outdir + ("_amp" if inps.overamp or inps.amp_file else "")
    os.makedirs(odir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(inps.input)))
    if not files:
        print("No files found."); sys.exit(1)

    # load mask once (if any)
    mask = None
    if inps.maskfile:
        print(f"Loading mask: {inps.maskfile}")
        img = isceobj.createImage(); img.load(files[0] + ".xml")
        width, length = img.width, img.length
        mask = np.fromfile(inps.maskfile, dtype=np.int8).reshape(length, width)

    # read highlight dates/pairs
    # highlight lists: [(names, color, label)]; --txt is --mark FILE:red
    def _names(f):
        out = set()
        for ln in open(f):
            for tok in ln.split():
                if tok[0].isdigit():
                    out |= {tok, tok.replace('-', '_')}
        return out
    groups = []
    for spec in ([f"{inps.date_txt}:red"] if inps.date_txt else []) + inps.mark:
        f, color, label = (spec.split(":", 2) + ["red", ""])[:3]
        if os.path.isfile(f):
            groups.append((_names(f), color, label or os.path.basename(f)))
        else:
            print(f"--mark: {f} not found, skipped")

    # gauge first file for layout
    img = isceobj.createImage(); img.load(files[0] + ".xml")
    width, length = img.width, img.length
    ipl, ppc, WIDTH = 20, 30, 30  # imgs/row, px/cm, artboard width
    n_rows = np.ceil((len(files) + (2 if inps.diff else 0)) / ipl)
    # layout [cm]: images of rW x rL in a grid, GAP between them, a label band LAB above each image
    HEAD, MARGIN, GAP, LAB = 1.2, 0.3, 0.12, 0.55
    rW = (WIDTH - 2 * MARGIN - GAP * (ipl - 1)) / ipl
    ratio = min(1.0, rW * ppc / width)   # display scale of the image pixels
    rW, rL = width * ratio / ppc, length * ratio / ppc
    PITCH_X, PITCH_Y = rW + GAP, LAB + rL + GAP
    LENGTH = HEAD + n_rows * PITCH_Y + MARGIN

    print(f"Total files: {len(files)} | {ipl} per row | ~{n_rows:.0f} rows")
    print(f"Image size: {width}x{length}px | Collage board: {WIDTH}x{LENGTH:.1f}cm")

    svg = f"""<?xml version="1.0" standalone="no"?>
    <!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"
    "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
    <svg width="{WIDTH}cm" height="{LENGTH}cm" version="1.1"
        xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
    <rect width="100%" height="100%" fill="white"/>"""

    k = max(1, int(round(1.0 / ratio)))  # decimation to the display size: mdx renders the small image

    def _block(a, k, phase):
        """k x k block average to the display size; phase: complex mean of exp(i 2pi a / wrap), so
        fringes are not aliased; 0 = no data (ignored; all-0 blocks stay 0)."""
        if k == 1:
            return a
        l2, w2 = a.shape[0] // k, a.shape[1] // k
        a = a[:l2 * k, :w2 * k].reshape(l2, k, w2, k)
        v = (a != 0) & np.isfinite(a)
        n = v.sum(axis=(1, 3))
        if phase:
            z = np.where(v, np.exp(1j * 2 * np.pi * np.nan_to_num(a) / inps.wrap), 0).sum(axis=(1, 3))
            out = np.angle(z) * inps.wrap / (2 * np.pi)
            out[(out == 0) & (n > 0)] = 1e-6                  # keep real zeros distinct from no data
        else:
            out = np.where(v, a, 0).sum(axis=(1, 3)) / np.maximum(n, 1)
        return np.where(n > 0, out, 0).astype(np.float32)

    def load(file, phase=True):
        """Bands of one file, block-averaged to the display size."""
        im = isceobj.createImage(); im.load(file + ".xml")
        w, l, nb = im.width, im.length, max(1, int(getattr(im, "bands", 1) or 1))
        data = np.fromfile(file, dtype=np.float32).reshape(l * nb, w)
        bands = [data[b::nb] for b in range(nb)]
        return [_block(bd, k, phase=phase and (b == nb - 1 and (inps.band == 2 or nb == 1)))
                for b, bd in enumerate(bands)]

    def draw(bands, name):
        """Render small bands with mdx (own workdir) and save <name>.png."""
        png = os.path.join(odir, f"{name}.png")
        nb = len(bands)
        if amp_bg is not None and nb == 1 and amp_bg.shape == bands[0].shape:
            bands = [amp_bg, bands[0]]                     # -> amplitude + phase, drawn like --amp
        if mask is not None:
            bands[-1] = bands[-1] * (_block(mask.astype(np.float32), k, phase=False) > 0.5)
        small = np.stack(bands, axis=1).reshape(-1, bands[0].shape[1]).astype(np.float32)   # BIL again
        w2 = bands[0].shape[1]
        wd = tempfile.mkdtemp(dir=odir, prefix=".mdx_")   # out.ppm is a fixed name: one workdir per image
        f2 = os.path.join(wd, "img")
        small.tofile(f2)
        if len(bands) == 1:
            cmd = f"mdx {f2} -s {w2} -ch1 -r4 -wrap {inps.wrap} -addr -{inps.wrap/2} -cmap CMY -P -workdir {wd}"
        elif not (inps.overamp or nb == 1):
            cmd = f"mdx {f2} -s {w2} -ch2 -r4 -rhdr {w2*4} -wrap {inps.wrap} -addr -{inps.wrap/2} -cmap CMY -P -workdir {wd}"
        else:
            cmd = f"mdx {f2} -s {w2} -amp -r4 -rtlr {w2*4} -CW -unw -r4 -rhdr {w2*4} -wrap {inps.wrap} -addr -{inps.wrap/2} -cmap CMY -P -workdir {wd}"
        runCmd(cmd + " > /dev/null")
        runCmd(f"{IM} {os.path.join(wd, 'out.ppm')} {png}")
        for x in os.listdir(wd):
            os.remove(os.path.join(wd, x))
        os.rmdir(wd)

    def render(file, pair):
        if os.path.isfile(os.path.join(odir, f"{pair}.png")) and not inps.redo:
            return
        draw(load(file), pair)

    amp_bg = None
    if inps.amp_file:
        im = isceobj.createImage(); im.load(inps.amp_file + ".xml")
        nb = max(1, int(getattr(im, "bands", 1) or 1))
        a = np.fromfile(inps.amp_file, dtype=np.float32).reshape(im.length * nb, im.width)[0::nb]
        if a.shape == (length, width):
            amp_bg = _block(a.astype(np.float32), k, phase=False)
        else:
            print(f"--amp-file {inps.amp_file}: {a.shape} differs from the images {(length, width)}, not used")

    from concurrent.futures import ThreadPoolExecutor
    pairs = [f.split("/")[inps.loc].split(".")[0] for f in files]
    labels = {}
    if not inps.diff:
        with ThreadPoolExecutor(inps.nproc) as ex:
            list(ex.map(render, files, pairs))
    else:
        # dates: real block means (the values are unwrapped), then differences to the previous date
        from datetime import datetime as _dt
        with ThreadPoolExecutor(inps.nproc) as ex:
            stack = np.array([b[0] for b in ex.map(lambda f: load(f, phase=False), files)])
        t = np.array([(_dt.strptime(p[:8], "%Y%m%d") - _dt.strptime(pairs[0][:8], "%Y%m%d")).days / 365.25 for p in pairs])
        ok = np.any(stack != 0, axis=0)                   # pixels with data on some date
        items = [(pairs[0], np.where(ok, 1e-6, 0).astype(np.float32))]
        for i in range(1, len(pairs)):
            d = stack[i] - stack[i - 1]
            items.append((pairs[i], np.where(ok & (d == 0), 1e-6, np.where(ok, d, 0)).astype(np.float32)))
        cum = stack[-1]
        a = stack.reshape(len(t), -1)
        tc = t - t.mean()
        rate = ((tc[:, None] * (a - a.mean(axis=0))).sum(axis=0) / (tc ** 2).sum()).reshape(cum.shape)
        items += [("cumulative", np.where(ok, cum, 0).astype(np.float32)),
                  ("rate", np.where(ok, rate, 0).astype(np.float32))]
        labels = {pairs[0]: f"{pairs[0]}|(reference)", "cumulative": f"cumulative|{pairs[-1]}",
                  "rate": f"linear rate|[{inps.unit}/yr]"}
        pairs = pairs + ["cumulative", "rate"]
        with ThreadPoolExecutor(inps.nproc) as ex:
            list(ex.map(lambda it: draw([it[1]], it[0]), items))

    for i, pair in enumerate(pairs):
        mdate, sdate, date = None, None, None
        if "_" in pair: mdate, sdate = pair.split("_")
        else: date = labels.get(pair, pair)

        # collage SVG entries
        if inps.collage:
            ii = int((i + 1 - 0.1) / ipl) + 1
            jj = i + 1 - (ii - 1) * ipl
            x0 = MARGIN + PITCH_X * (jj - 1)
            y0 = HEAD + PITCH_Y * (ii - 1) + LAB          # image top; its label is in the band above
            name = f"{mdate}_{sdate}" if mdate else pair
            hit = [(c, i) for i, (n, c, _) in enumerate(groups) if name in n]
            font_color = f";fill:{hit[-1][0]}" if hit else ""
            add_box = "".join(f'<rect fill="none" stroke="{c}" stroke-width="{2 + i}" x="{x0}cm" y="{y0}cm" width="{rW}cm" height="{rL}cm"/>'
                              for c, i in hit)
            two = (f"{mdate}_", sdate) if mdate else (date.split("|") + [""])[:2]
            label = f'<tspan x="{x0}cm" dy="0">{two[0]}</tspan><tspan x="{x0}cm" dy="1em">{two[1]}</tspan>'
            img_svg = f'''<image xlink:href="{pair}.png" x="{x0}cm" y="{y0}cm" width="{rW}cm" height="{rL}cm"/>
                {add_box}
                <text x="{x0}cm" y="{y0-LAB+0.22}cm" style="font-family:Times;font-size:8px{font_color};">{label}</text>'''
            svg += img_svg

    # colorbar, top right of the collage
    cb_w, cb_l = 100, 20
    wd = tempfile.mkdtemp(dir=odir, prefix=".mdx_")
    cb = np.ones((cb_l, cb_w), np.float32) * np.linspace(-inps.wrap/2, inps.wrap/2, cb_w, dtype=np.float32)[None,:]
    cb.astype(np.float32).tofile(os.path.join(wd, "colorbar"))
    runCmd(f"mdx {os.path.join(wd,'colorbar')} -s {cb_w} -cmap cmy -wrap {inps.wrap} -addr -{inps.wrap/2} -P -workdir {wd} > /dev/null")
    cbar = f"colorbar_-{inps.wrap/2:g}_{inps.wrap/2:g}.png"
    runCmd(f"{IM} {os.path.join(wd, 'out.ppm')} {os.path.join(odir, cbar)}")
    runCmd(f"rm -r {wd}")
    cbx, cbw = WIDTH - 5.0, 4.0
    svg += f'''<text x="0.3cm" y="0.6cm" style="font-family:Times;font-size:12px;">{inps.input}  ({len(files)} images{"; each date minus the previous; last two: cumulative, linear rate" if inps.diff else ""})</text>''' + "".join(
        f'<text x="{10 + 6*i}cm" y="0.6cm" style="font-family:Times;font-size:12px;fill:{c};">&#9633; {lab} ({len({x.replace("-", "_") for x in n})})</text>'
        for i, (n, c, lab) in enumerate(groups)) + f'''
        <image xlink:href="{cbar}" x="{cbx}cm" y="0.2cm" width="{cbw}cm" height="0.4cm" preserveAspectRatio="none"/>
        <text x="{cbx}cm" y="0.9cm" style="font-family:Times;font-size:10px;" text-anchor="middle">{-inps.wrap/2:g}</text>
        <text x="{cbx+cbw/2}cm" y="0.9cm" style="font-family:Times;font-size:10px;" text-anchor="middle">0</text>
        <text x="{cbx+cbw}cm" y="0.9cm" style="font-family:Times;font-size:10px;" text-anchor="middle">{inps.wrap/2:g} {inps.unit} (wrapped)</text>'''
    svg += "</svg>"
    if inps.svg:
        with open(os.path.join(odir, "collage.svg"), "w") as f: f.write(svg)

    html_file = os.path.join(odir, "collage.html")
    with open(html_file, "w") as f:
        f.write("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Collage</title></head><body style='margin:0;'>\n"
                + svg + "\n</body></html>\n")

    print(f"Done. HTML preview: {html_file}")
