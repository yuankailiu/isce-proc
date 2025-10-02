#%%
import numpy as np
import matplotlib.pyplot as plt

from mintpy.utils import readfile
from mintpy.utils import plot as pp


raw_file = 'merged/interferograms/20141116_20141128/filt_fine.unw'
ion_file = 'ion/20141116_20141128/ion_cal/filt.ion'

bramp_file1 = 'ion_burst_ramp_merged_dates/20141116.float'
bramp_file2 = 'ion_burst_ramp_merged_dates/20141128.float'


####
inity = 1
initx = 2
sk   = 10

amp     = readfile.read(raw_file, datasetName='band1')[0][::sk, ::sk][1:,1:]
raw_ifg = readfile.read(raw_file, datasetName='band2')[0][::sk, ::sk][1:,1:]
ion_ifg = readfile.read(ion_file, datasetName='band2')[0]

bramp_1 = readfile.read(bramp_file1)[0][::sk, ::sk][1:,1:]
bramp_2 = readfile.read(bramp_file2)[0][::sk, ::sk][1:,1:]
bramp_ifg = bramp_1 - bramp_2

print(raw_ifg.shape)
print(ion_ifg.shape)
print(bramp_ifg.shape)

###
# %%
cmy = pp.ColormapExt('cmy').colormap
cmap = 'RdBu'

fig, axs = plt.subplots(ncols=5, sharey=True, figsize=[10,8], gridspec_kw={'wspace':0, 'hspace':0.05}, tight_layout=True)

im = axs[0].imshow(raw_ifg%10, cmap=cmap, interpolation='none', vmin=0, vmax=10)
plt.colorbar(im, ax=axs[0], orientation='horizontal', shrink=0.6, aspect=6, pad=0.01)
axs[0].set_title('Raw')

im = axs[1].imshow(ion_ifg%10, cmap=cmap, interpolation='none', vmin=0, vmax=10)
plt.colorbar(im, ax=axs[1], orientation='horizontal', shrink=0.6, aspect=6, pad=0.01)
axs[1].set_title('ion')

im = axs[2].imshow(bramp_ifg, cmap=cmap, interpolation='none')
plt.colorbar(im, ax=axs[2], orientation='horizontal', shrink=0.6, aspect=6, pad=0.01)
axs[2].set_title('Burst ramp')


im = axs[3].imshow((raw_ifg-ion_ifg)%10, cmap=cmap, interpolation='none', vmin=0, vmax=10)
plt.colorbar(im, ax=axs[3], orientation='horizontal', shrink=0.6, aspect=6, pad=0.01)
axs[3].set_title('Raw - ion')

im = axs[4].imshow((raw_ifg-ion_ifg-bramp_ifg)%10, cmap=cmap, interpolation='none', vmin=0, vmax=10)
plt.colorbar(im, ax=axs[4], orientation='horizontal', shrink=0.6, aspect=6, pad=0.01)
axs[4].set_title('Raw - ion - burst ramp')

for ax in axs:
    ax.axis('off')

plt.savefig('test_ion_corr.png', dpi=300, bbox_inches='tight', transparent=True)
plt.show()

# %%
