"""Parse and extract Apple "rkosftab" firmware tables.

The AirPods UARP payloads FTAB, R1FW, STFC and STFW are firmware tables: a
32-byte header, the magic "rkosftab" at offset 0x20, then 16-byte entries of
tag (4 ASCII bytes), offset (u32 LE), size (u32 LE) and padding. Offsets are
relative to the table start. Some entries are tables themselves.

    python3 ftab.py TABLE list
    python3 ftab.py TABLE extract TAG OUT      (first entry with that tag)
    python3 ftab.py firmware.uarp list --payload FTAB
    python3 ftab.py firmware.uarp extract acib acib.bin --payload FTAB
    python3 ftab.py firmware.uarp payloads     (the SuperBinary's own records)
    python3 ftab.py firmware.uarp payload CTAB ctab.bin
"""
import struct
import sys

MAGIC = b"rkosftab"


def entries(table):
    if table[0x20:0x28] != MAGIC:
        raise ValueError("not an rkosftab table")
    out = []
    off = 0x30
    while off + 16 <= len(table):
        tag, eoff, size, _pad = struct.unpack_from("<4sIII", table, off)
        if not all(32 <= c < 127 for c in tag):
            break
        if eoff == 0 and size == 0 and out and tag not in (b"sbd2",):
            break
        out.append((tag.decode("ascii"), eoff, size))
        off += 16
    return out


def uarp_payloads(blob):
    """Every payload record of a SuperBinary, in the order its header lists them."""
    phoff, phlen = struct.unpack_from(">2I", blob, 36)
    out = []
    off = phoff
    while off < phoff + phlen:
        plen, tag = struct.unpack_from(">I4s", blob, off)
        poff, plength = struct.unpack_from(">2I", blob, off + 32)
        out.append((tag.decode("ascii", "replace"), poff, plength))
        off += plen
    return out


def uarp_payload(blob, want):
    for tag, poff, plength in uarp_payloads(blob):
        if tag == want:
            return blob[poff:poff + plength]
    raise KeyError(want)


def show(table, depth=0):
    for tag, eoff, size in entries(table):
        data = table[eoff:eoff + size]
        nested = len(data) > 0x28 and data[0x20:0x28] == MAGIC
        head = data[:8].hex(" ")
        kind = "table" if nested else ""
        print(f"{'  ' * depth}{tag:4s} off={eoff:#09x} size={size:9d} {kind} {head}")
        if nested:
            show(data, depth + 1)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    payload = None
    if "--payload" in argv:
        payload = argv[argv.index("--payload") + 1]
        args = [a for a in args if a != payload]
    if len(args) < 2:
        print(__doc__)
        return 2
    blob = open(args[0], "rb").read()
    cmd = args[1]
    if cmd == "payloads":
        for tag, poff, plength in uarp_payloads(blob):
            print(f"{tag:4s} off={poff:#010x} size={plength:9d}")
        return 0
    if cmd == "payload" and len(args) >= 4:
        data = uarp_payload(blob, args[2])
        open(args[3], "wb").write(data)
        print(f"wrote {len(data)} bytes of payload {args[2]} to {args[3]}")
        return 0
    table = uarp_payload(blob, payload) if payload else blob
    if cmd == "list":
        show(table)
        return 0
    if cmd == "extract" and len(args) >= 4:
        for tag, eoff, size in entries(table):
            if tag == args[2]:
                open(args[3], "wb").write(table[eoff:eoff + size])
                print(f"wrote {size} bytes of {tag} to {args[3]}")
                return 0
        print(f"no entry {args[2]}")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
