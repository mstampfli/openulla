#!/bin/sh
# Fetch the current AirPods firmware asset for one model from Apple's public
# asset catalog and unpack it. The result stays in an ignored directory; the
# firmware is Apple's and is not redistributed by this repository.
#
# Usage: tools/fetch_firmware.sh [model] [outdir]
#   model: UARP asset name, default A3048 (AirPods Pro 2 USB-C, right earbud).
#          A2968 is the USB-C charging case. A3047 (left earbud) returned 403
#          at the time of writing.
# Needs curl, unzip, python3.
set -eu
model=${1:-A3048}
out=${2:-firmware}
asset="com_apple_MobileAsset_UARP_$model"
mkdir -p "$out"
xml="$out/$asset.xml"
curl -fsS -o "$xml" "https://mesu.apple.com/assets/$asset/$asset.xml"
url=$(python3 - "$xml" <<'EOF'
import plistlib, sys
d = plistlib.load(open(sys.argv[1], "rb"))
a = d["Assets"][0]
print(a["__BaseURL"] + a["__RelativePath"])
print(a["Build"], a["_DownloadSize"], file=sys.stderr)
EOF
)
zip="$out/$model.zip"
echo "downloading $url"
curl -fsS -o "$zip" "$url"
rm -rf "$out/$model"
mkdir -p "$out/$model"
unzip -q "$zip" -d "$out/$model"
echo "unpacked to $out/$model"
find "$out/$model" -name firmware.uarp
