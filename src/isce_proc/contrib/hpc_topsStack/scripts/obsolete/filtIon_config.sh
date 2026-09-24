#!/bin/bash
# Usage: ./append_config.sh

TRACK="a120"   # <-- change this track number when needed
CONFIG_DIR="/resnick/groups/simonsgroup/ykliu/chile/${TRACK}/hpc_topsStack/configs"

# Define input files here (optional)
# Uncomment if needed:
PAIR_FILE="/resnick/groups/simonsgroup/ykliu/chile/${TRACK}/hpc_topsStack/pairs_diff_starting_ranges.txt"
#MASK_FILE="/resnick/groups/simonsgroup/ykliu/chile/${TRACK}/hpc_topsStack/inputs/otsu_msk.rdr"

# Common lines for config_filtIon_*
WBD_LINE="wbdfile : /resnick/groups/simonsgroup/ykliu/chile/dem/wbd_1_arcsec/swbdLat_S40_S07_Lon_W082_W062.wbd"
ITER_LINE="iteration : 5"
FILL_LINE="fill : nearest"

# Line for swath alignment
SWATH_LINE="swath_align : True"

# Line for config_burstRampIon_*
BURST_MASK_LINE="maskfile : /resnick/groups/simonsgroup/ykliu/chile/${TRACK}/hpc_topsStack/merged/geom_reference/waterBody.rdr"

echo ">>> Updating config_filtIon_* ..."
for f in "$CONFIG_DIR"/config_filtIon_*; do
    [ -f "$f" ] || continue
    printf "%s\n%s\n%s\n" "$WBD_LINE" "$ITER_LINE" "$FILL_LINE" >> "$f"
    if [ -n "$MASK_FILE" ] && [ -f "$MASK_FILE" ]; then
        echo "maskfile : $MASK_FILE" >> "$f"
    fi
    echo "Updated $f"
done

if [ -n "$PAIR_FILE" ] && [ -f "$PAIR_FILE" ]; then
    echo ">>> Appending swath_align : True for unique pairs in $PAIR_FILE ..."
    grep -E '^[0-9]{8}_[0-9]{8}$' "$PAIR_FILE" | sort -u | while read -r pair; do
        cfg="$CONFIG_DIR/config_filtIon_${pair}"
        if [ -f "$cfg" ]; then
            echo "$SWATH_LINE" >> "$cfg"
            echo "Appended swath_align to $cfg"
        fi
    done
fi

echo ">>> Updating config_burstRampIon_* ..."
for f in "$CONFIG_DIR"/config_burstRampIon_*; do
    [ -f "$f" ] || continue
    echo "$BURST_MASK_LINE" >> "$f"
    echo "Updated $f"
done

echo ">>> All done."

