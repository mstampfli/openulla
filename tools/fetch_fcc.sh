#!/bin/sh
# Download the FCC exhibits that describe the Narrowband UNII radio of the
# AirPods Pro 2 USB-C left earbud (BCG-A3047) and the Apple Vision Pro
# (BCGA2117), then extract their text. Needs curl and pdftotext (poppler).
#
# Usage: tools/fetch_fcc.sh [outdir]   (default: fcc)
set -eu
out=${1:-fcc}
mkdir -p "$out"
ua='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

reports='
BCG-A3047/Test-Report/NB-UNII-BDR-Test-Report-6764877
BCG-A3047/Test-Report/NB-UNII-HDR-Test-Report-6764878
BCG-A3047/Test-Report/NB-UNII-LE-Test-Report-6764879
BCG-A3047/Test-Report/NB-UNII-5-Test-Report-1-7595478
BCG-A3047/Test-Report/NB-UNII-5-Test-Report-2-7595481
BCG-A3047/Test-Report/Bluetooth-HDR-Test-Report-6764849
BCG-A3047/Test-Report/Antenna-Gain-Report-7553974
BCGA2117/Test-Report/NB-UNII-BDR-Test-Report-6962411
BCGA2117/Test-Report/NB-UNII-HDR-Test-Report-6962412
BCGA2117/Test-Report/NB-UNII-LE-Test-Report-6962414
BCGA2117/Test-Report/NB-UNII-5-Test-Report-1-7587864
BCGA2117/Test-Report/NB-UNII-5-Test-Report-2-7587866
BCGA2117/Test-Report/NB-UNII-5-Test-Report-3-7587867
'

for path in $reports; do
    id=${path%%/*}
    name=${path##*/}
    pdf="$out/${id}_${name}.pdf"
    if [ ! -s "$pdf" ]; then
        echo "fetching $path"
        curl -fsSL -A "$ua" -o "$pdf" "https://fccid.io/$path.pdf"
    fi
    pdftotext -layout "$pdf" "${pdf%.pdf}.txt"
done
echo "done: $(ls "$out"/*.txt | wc -l) reports in $out"
