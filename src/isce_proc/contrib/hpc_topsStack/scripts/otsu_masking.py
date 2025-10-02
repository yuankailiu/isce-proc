import os
import re
import sys
from datetime import datetime

import numpy as np
import h5py
import matplotlib.pyplot as plt

from osgeo import gdal
from skimage.transform import resize
from skimage.filters import threshold_multiotsu
from skimage.measure import label, regionprops

# ISCE2 imports
sys.path.append('/central/home/ykliu/tools/isce2/install/packages')
import isce
import isceobj
from applications.gdal2isce_xml import gdal2isce_xml
from isceobj.Alos2Proc.Alos2ProcPublic import waterBodyRadar
from isceobj.TopsProc.runIon import adaptive_gaussian, weight_fitting

# Logging / Matplotlib config
import matplotlib as mpl
mpl.set_loglevel("warning")  # 或 "error"


# --------------------------
# Utility functions
# --------------------------

def read_h5_dataset(file_path, datasetName=None):
    """Read a dataset from an HDF5 file."""
    with h5py.File(file_path, 'r') as f:
        if datasetName is None:
            for key in f.keys():
                if isinstance(f[key], h5py.Dataset):
                    datasetName = key
                    break
        print(datasetName)
        return f[datasetName][()]


def uint8_to_int16(arr):
    """Convert raw uint8 mask to semantic int16."""
    semantic_arr = arr.astype(np.int16)
    semantic_arr[arr == 0] = 0     # land
    semantic_arr[arr == 255] = -1  # water
    semantic_arr[arr == 254] = -2  # no data
    return semantic_arr


def wrap_phase(ifg):
    """Return wrapped phase in [-pi, pi]."""
    return np.angle(np.exp(-1j * ifg))


def calc_date_interval(d12_str):
    """Return date interval (6 or 12 days) given a pair string."""
    delimiters = r"[_ -]"
    parts = re.split(delimiters, d12_str)
    if len(parts) != 2:
        print(f"Error: Input string '{d12_str}' not valid.")
        return None

    d1, d2 = map(lambda s: datetime.strptime(s, "%Y%m%d"), parts)
    delta = abs((d2 - d1).days)

    if delta % 12 == 0:
        return 12
    elif delta % 6 == 0:
        return 6
    return delta


def read_isce_band(filename, band=1):
    """Read one band from an ISCE raster file."""
    gdal.UseExceptions()
    ds = gdal.Open(str(filename), gdal.GA_ReadOnly)
    arr = ds.GetRasterBand(band).ReadAsArray()
    no_data = ds.GetRasterBand(band).GetNoDataValue()
    dtype_str = gdal.GetDataTypeName(ds.GetRasterBand(band).DataType)

    dtype_map = {
        'Byte': np.uint8,
        'Int16': np.int16,
        'Int32': np.int32,
    }
    dtype = dtype_map.get(dtype_str, np.int16)

    width, height = ds.RasterXSize, ds.RasterYSize
    ds = None
    return arr, (height, width), dtype, dtype_str, no_data


def find_largest_component(mask):
    """Keep the largest connected component."""
    labeled = label(mask)
    regions = regionprops(labeled)
    largest = max(regions, key=lambda r: r.area)
    return (labeled == largest.label), labeled


def filtion(ion, cor, size_min=100, size_max=200, corThresholdIon=0.85, pp=14):
    """Apply adaptive Gaussian filtering to ionospheric phase."""
    length, width = ion.shape
    ion_fit = weight_fitting(ion, cor, width, length, 1, 1, 1, 1, 2, corThresholdIon)
    ion -= ion_fit * (ion != 0)
    filt = adaptive_gaussian(ion, cor**pp, size_max, size_min)
    filt += ion_fit * (filt != 0)
    return filt


def resize_to_match(arr, target_shape):
    """Resize array to match target shape (float32)."""
    return resize(arr, target_shape, order=1, mode='edge',
                  anti_aliasing=False, preserve_range=True).astype('float32')


def make_water_mask(amp, percentile=40):
    """Generate water mask using amplitude percentile threshold."""
    return (amp > np.percentile(amp, percentile)) & np.isfinite(amp)


def create_isce_xml_header(output_rdr, new_nx, new_ny, original_isce_dtype, original_nodata=None):
    """Create or update ISCE XML header for a .rdr file."""
    img = isceobj.createImage()
    img.filename = output_rdr
    img.width, img.length = new_nx, new_ny
    img.accessMode = 'read'
    img.dataType = original_isce_dtype
    img.imageType = 'image'

    img.startingRange = 0.0
    img.deltaRange = 1.0
    img.startingAzimuth = float(new_ny - 1)
    img.deltaAzimuth = -1.0

    if original_nodata is not None:
        img.noDataValue = original_nodata

    img.renderHdr()


def multilook_and_save_mask(infile, outfile, dst_width, dst_height, method='nearest'):
    """Resize an ISCE raster mask and save with ISCE XML header."""
    arr, dim, dtype, dtype_str, no_data = read_isce_band(infile)
    order_map = {'nearest': 0, 'bilinear': 1, 'bicubic': 3}

    target_shape = (dst_height, dst_width)
    out = resize(arr, target_shape, order=order_map[method],
                 preserve_range=True, anti_aliasing=False).astype(dtype)
    out.tofile(outfile)
    create_isce_xml_header(outfile, dst_width, dst_height, dtype_str, no_data)

    print(f"Resized mask saved: {outfile} ({dst_height} lines, {dst_width} samples)")
    print(f"ISCE XML header generated: {outfile}.xml")
    return out


def make_radar_wbd(geom_basedir, input_wbd_file, ftype='Body'):
    """Create water body file in SAR radar coordinates."""
    radar_wbd_output_path = os.path.join(geom_basedir, f'water{ftype}.rdr')
    if os.path.exists(radar_wbd_output_path):
        print(f'Radar water body exists: {radar_wbd_output_path}. Skipping.')
    else:
        print(f'Generating radar water body: {radar_wbd_output_path}')
        lat_file, lon_file = (os.path.join(geom_basedir, f) for f in ['lat.rdr', 'lon.rdr'])
        if not os.path.exists(lat_file + '.xml'): gdal2isce_xml(lat_file + '.vrt')
        if not os.path.exists(lon_file + '.xml'): gdal2isce_xml(lon_file + '.vrt')
        waterBodyRadar(lat_file, lon_file, input_wbd_file, radar_wbd_output_path)
        print(f'Successfully created: {radar_wbd_output_path}')
    os.system(f'fixImageXml.py -i {radar_wbd_output_path} -f')
    return radar_wbd_output_path


# --------------------------
# Example workflow
# --------------------------

base_dir = os.path.abspath('../')
pair = '20141030_20141217'

reffile = os.path.abspath('../merged/geom_reference/waterBody.rdr')
outfile = 'otsu_msk.rdr'

infile = f'{base_dir}/ion/{pair}/ion_cal/raw_no_projection.ion'
data = read_isce_band(infile, band=2)[0]
data[data == 0] = np.nan

# Multi-Otsu thresholds
thresholds = threshold_multiotsu(data[~np.isnan(data)], classes=5)
regions = np.digitize(data, bins=thresholds)
labels = label(regions)

# Plot raw ionosphere
vmin, vmax = np.nanpercentile(data.flatten(), [10, 90])
plt.figure()
plt.imshow(data, cmap='jet', vmin=vmin, vmax=vmax)
plt.colorbar()
plt.title('Raw ionospheric phase')
plt.show()

# Plot mask
mask = (regions == 1) + (regions == 2)
plt.figure()
plt.imshow(mask, cmap='gray')
plt.colorbar()
plt.title('Labels')
plt.show()

# Save threshold mask
_, dim, dtype, dtype_str, no_data = read_isce_band(reffile)
mask.astype(np.uint8).tofile(outfile)
create_isce_xml_header(outfile, dim[1], dim[0], dtype_str, no_data)
print(f"Threshold mask saved: {outfile} ({dim[0]} lines, {dim[1]} samples)")

try:
    os.remove("isce.log")
    print("isce.log removed.")
except FileNotFoundError:
    pass