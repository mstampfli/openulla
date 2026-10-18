"""Parse an Apple UARP SuperBinary firmware container.

    python3 uarp.py firmware.uarp list
    python3 uarp.py firmware.uarp extract TAG out.bin   (first payload with that tag)
    python3 uarp.py firmware.uarp owner OFFSET          (which payload holds a byte offset)

Header layout (big endian, format version 3, header length 44):
    format, header_length, total_length, version[4], metadata_offset,
    metadata_length, payload_headers_offset, payload_headers_length
Each payload header (40 bytes): header_length, tag[4], version[4],
    metadata_offset, metadata_length, payload_offset, payload_length.
"""
import math
import struct
import sys
from collections import Counter


def payloads(blob):
    (fmt, _hlen, total, v0, v1, v2, v3, _moff, _mlen, phoff, phlen) = struct.unpack_from(
        ">11I", blob, 0
    )
    out = []
    off = phoff
    while off < phoff + phlen:
        plen, tag = struct.unpack_from(">I4s", blob, off)
        version = struct.unpack_from(">4I", blob, off + 8)
        pmoff, pmlen, poff, plength = struct.unpack_from(">4I", blob, off + 24)
        out.append({
            "tag": tag.decode("ascii", "replace"),
            "version": ".".join(str(x) for x in version),
            "offset": poff,
            "length": plength,
            "meta_offset": pmoff,
            "meta_length": pmlen,
        })
        off += plen
    return {"format": fmt, "total": total, "version": f"{v0}.{v1}.{v2}.{v3}"}, out


def entropy(data):
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    blob = open(argv[1], "rb").read()
    head, items = payloads(blob)
    cmd = argv[2]
    if cmd == "list":
        print(f"format {head['format']} version {head['version']} "
              f"length {head['total']} ({len(blob)} bytes in file)")
        print(f"{'tag':5s} {'version':>28s} {'offset':>10s} {'length':>10s} {'entropy':>7s}  head")
        for p in items:
            data = blob[p["offset"]:p["offset"] + p["length"]]
            head16 = data[:12].hex(" ")
            print(f"{p['tag']:5s} {p['version']:>28s} {p['offset']:>10} {p['length']:>10} "
                  f"{entropy(data[:200000]):7.2f}  {head16}")
    elif cmd == "extract" and len(argv) >= 5:
        tag = argv[3]
        for p in items:
            if p["tag"] == tag:
                open(argv[4], "wb").write(blob[p["offset"]:p["offset"] + p["length"]])
                print(f"wrote {p['length']} bytes of {tag} to {argv[4]}")
                return 0
        print(f"no payload tagged {tag}")
        return 1
    elif cmd == "owner" and len(argv) >= 4:
        x = int(argv[3], 0)
        for p in items:
            if p["offset"] <= x < p["offset"] + p["length"]:
                print(f"{p['tag']} +{x - p['offset']:#x}")
                return 0
        print("header or gap")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
