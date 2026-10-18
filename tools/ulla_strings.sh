#!/bin/sh
# List the ULLA, UTP, HDR and link-manager identifiers in an AirPods firmware
# image. Output is identifiers only; the firmware itself stays where it is.
#
# Usage: tools/ulla_strings.sh firmware.uarp
set -eu
fw=${1:?firmware.uarp}
strings -n 6 "$fw" \
    | grep -i 'ulla\|utp_\|_utp\|UTP_\|hdr4\|hdr8\|hdrp\|lm_ulla\|UNII\|Allen' \
    | grep -v 'NULL\|null\|Null\|FULL\|full\|Full\|PULL\|pull\|Pull\|BULL\|SKULL\|HULL\|DULL\|MULL\|GULL\|CULL\|llullu\|lullu\|ullul' \
    | sort -u
