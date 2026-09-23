#!/bin/bash

plot_imgs.py -i 'ion/*_*/ion_cal/filt.ion'            --redo --loc -3 --band 2 --out pic/img_ion         --txt pairs_diff_starting_ranges.txt --amp
plot_imgs.py -i 'ion_dates/*.ion'                     --redo --loc  1 --band 1 --out pic/img_ion_dates   --wrap 6.28
plot_imgs.py -i 'ion_azshift_dates/*.ion'             --redo --loc  1 --band 1 --out pic/img_azshiftDate --wrap 0.00628
plot_imgs.py -i 'ion_burst_ramp_merged_dates/*.float' --redo --loc -1 --band 1 --out pic/img_ionRampDate --wrap 0.0628
#plot_imgs.py -i 'merged/interferograms/*_*/filt_fine.unw' --redo --loc -2 --band 2 --out pic/img_unw --amp

