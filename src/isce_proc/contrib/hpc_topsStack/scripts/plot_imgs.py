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
                        help="Replot all .tif images")
    parser.add_argument("-a", "--amp", dest="overamp", action="store_true",
                        help="Overlay amplitude (only valid for band=2)")
    parser.add_argument("-m", "--mask", dest="maskfile", default=None,
                        help="Optional mask raster (same size, e.g. waterBody.rdr, 0=water,1=land)")
    parser.add_argument("-t", "--txt", dest="date_txt", default=None,
                        help="Text file listing dates/pairs to highlight")
    parser.add_argument("-c", "--collage", dest="collage", action="store_true", default=True,
                        help="Collage .tifs into SVG/HTML (default: %(default)s)")
    if len(sys.argv) <= 1:
        parser.print_help(); sys.exit(1)
    return parser.parse_args()


if __name__ == "__main__":
    inps = cmdLineParse()

    odir = inps.outdir + ("_amp" if inps.overamp else "")
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
    marks = []
    if inps.date_txt:
        with open(inps.date_txt) as f:
            marks = [ln.strip() for ln in f if ln.strip() and ln[0].isdigit()]
        marks = list(set(marks))

    # gauge first file for layout
    img = isceobj.createImage(); img.load(files[0] + ".xml")
    width, length = img.width, img.length
    ipl, ppc, WIDTH = 20, 30, 30  # imgs/row, px/cm, artboard width
    n_rows = np.ceil(len(files) / ipl)
    ratio = min(1.0, WIDTH * ppc / (ipl * width))  # scaling factor
    LENGTH = (length * ratio * n_rows) / ppc
    rW, rL = width * ratio / ppc, length * ratio / ppc

    print(f"Total files: {len(files)} | {ipl} per row | ~{n_rows:.0f} rows")
    print(f"Image size: {width}x{length}px | Collage board: {WIDTH}x{LENGTH:.1f}cm")

    svg = f"""<?xml version="1.0" standalone="no"?>
    <!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"
    "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
    <svg width="{WIDTH}cm" height="{LENGTH}cm" version="1.1"
        xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">"""

    tmp_files = []  # temporary masked files to cleanup
    wdir = tempfile.mkdtemp(dir=odir, prefix=".mdx_")  # own mdx workdir: out.ppm is a fixed name, so runs sharing odir would collide

    for i, file in enumerate(files):
        pair = file.split("/")[inps.loc].split(".")[0]
        mdate, sdate, date = None, None, None
        if "_" in pair: mdate, sdate = pair.split("_")
        else: date = pair

        # replot to tif
        if inps.redo:
            img = isceobj.createImage(); img.load(file + ".xml")
            width, length = img.width, img.length
            file_to_plot = file

            if mask is not None:
                data = np.fromfile(file, dtype=np.float32).reshape(length, width)
                data = (data * mask).astype(np.float32)
                tmp = tempfile.NamedTemporaryFile(delete=False, dir="/dev/shm", suffix=".ion")
                data.tofile(tmp.name)
                file_to_plot = tmp.name
                tmp_files.append(tmp.name)

            if inps.band == 1:
                cmd = f"mdx {file_to_plot} -s {width} -ch1 -r4 -wrap {inps.wrap} -addr -{inps.wrap/2} -cmap CMY -P -workdir {wdir}"
            elif inps.band == 2:
                if not inps.overamp:
                    cmd = f"mdx {file_to_plot} -s {width} -ch2 -r4 -rhdr {width*4} -wrap {inps.wrap} -addr -{inps.wrap/2} -cmap CMY -P -workdir {wdir}"
                else:
                    cmd = f"mdx {file_to_plot} -s {width} -amp -r4 -rtlr {width*4} -CW -unw -r4 -rhdr {width*4} -wrap {inps.wrap} -addr -{inps.wrap/2} -cmap CMY -P -workdir {wdir}"
            runCmd(cmd)

            # resize + compress to keep files small
            ppm = os.path.join(wdir, "out.ppm")
            tif = os.path.join(odir, f"{pair}.tif")
            resize = f"-resize {100.0*ratio}%"
            runCmd(f"convert {ppm} {resize} -compress LZW {tif}")
            os.remove(ppm)

        # collage SVG entries
        if inps.collage:
            ii = int((i + 1 - 0.1) / ipl) + 1
            jj = i + 1 - (ii - 1) * ipl
            x0 = rW * 0.85 * (jj - 1) + rW / 5
            y0 = rL * 0.82 * (ii - 1) + rL / 8
            font_color, add_box = "", ""
            if mdate and sdate:
                if any(x in marks for x in [f"{mdate}_{sdate}", f"{sdate}_{mdate}", f"{mdate}-{sdate}", f"{sdate}-{mdate}"]):
                    font_color = ";fill:red"
                    add_box = f'<rect fill="none" stroke="red" stroke-width="2" x="{x0}cm" y="{y0}cm" width="{rW}cm" height="{rL}cm"/>'
                img_svg = f'''<image xlink:href="{pair}.tif" x="{x0}cm" y="{y0}cm"/>
                    {add_box}
                    <text x="{x0}cm" y="{y0+rW*0.1}cm" style="font-family:Times;font-size:8px{font_color};">
                    <tspan x="{x0}cm" dy="0">{mdate}_</tspan><tspan x="{x0}cm" dy="1em">{sdate}</tspan></text>'''
            else:
                if date in marks: font_color = ";fill:red"
                img_svg = f'''<image xlink:href="{pair}.tif" x="{x0}cm" y="{y0}cm"/>
                    <text x="{x0}cm" y="{y0+rW*0.1}cm" style="font-family:Times;font-size:8px{font_color};">{date}</text>'''
            svg += img_svg

    svg += "</svg>"
    with open(os.path.join(odir, "collage.svg"), "w") as f: f.write(svg)

    # cleanup tmp RAM files
    for t in tmp_files:
        try: os.remove(t)
        except: pass

    # colorbar
    cb_w, cb_l = 100, 20
    cb = np.ones((cb_l, cb_w), np.float32) * np.linspace(-inps.wrap/2, inps.wrap/2, cb_w, dtype=np.float32)[None,:]
    cb.astype(np.float32).tofile(os.path.join(odir, "colorbar"))
    runCmd(f"mdx {os.path.join(odir,'colorbar')} -s {cb_w} -cmap cmy -wrap {inps.wrap} -addr -{inps.wrap/2} -P -workdir {wdir}")
    ppm = os.path.join(wdir, "out.ppm")
    tif = os.path.join(odir, f"colorbar_-{inps.wrap/2}_{inps.wrap/2}.tiff")
    runCmd(f"convert {ppm} -compress LZW {tif}")
    runCmd(f"rm {os.path.join(odir,'colorbar')} {ppm}")
    os.rmdir(wdir)

    # HTML
    html_file = os.path.join(odir, "collage.html")
    runCmd(f"mogrify -format png {odir}/*.tif")
    runCmd(f"cp {os.path.join(odir,'collage.svg')} {html_file}")
    with open(html_file, "r+") as f:
        content = f.read()
        f.seek(0)
        f.write("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Collage</title></head><body style='margin:0;'>\n" + content)
    with open(html_file, "a") as f: f.write("</body></html>\n")
    runCmd(f"sed -i 's/\\.tif/\\.png/g' {html_file}")

    print(f"Done. HTML preview: {html_file}")
