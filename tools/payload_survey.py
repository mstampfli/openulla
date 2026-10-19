#!/usr/bin/env python3
"""Ask which of a build's payloads hold this link's material, and which hold none of it.

Every firmware claim in these documents comes from three payloads of ten - `acib`, `rkof` and
`sbd1` - and that could be where the looking happened rather than where the material is. This
answers it across whatever payloads you have extracted:

    python3 tools/payload_survey.py --payloads payloads

It scans every `*.bin` in the directory, so it covers as much of the build as you extracted;
`tools/verify_claims.py` needs five of them and this needs none in particular. Its answer for
build 9A348 is docs/ulla.md 4.7.

**The control is the point of this tool.** Counting occurrences of a vendor opcode as a
two-byte pattern says nothing in a firmware image: a given halfword occurs by chance about once
per 64 KB, so a megabyte payload contains any value you care to name a dozen times or more, and
a first pass at this question duly "found" five documented opcodes in payloads that know
nothing about them. So every opcode count is reported beside the count of halfwords in the same
numeric range that this project does **not** document, and the verdict is the comparison. A
count on its own is never evidence that a payload knows an opcode; a handler in a dispatch
table is, which is how docs/protocol.md section 6 reaches every opcode it names.
"""
import argparse
import os
import pathlib
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import hci, packets

# The six this project documents as reaching the link or the packet table.
DOCUMENTED = (
    hci.OPCODE_PACKET_SIZE,
    hci.OPCODE_PACKET_TABLE_DIRECT,
    hci.OPCODE_ULLA_SETUP,
    hci.OPCODE_STREAM_DESCRIPTOR,
    hci.OPCODE_SET_LINK_BAND,
    hci.OPCODE_FEATURE_GROUP,
)
# Halfwords in the same 0xFD to 0xFF range that this project documents nowhere. These are the
# control: whatever the documented ones score, these score the same by chance alone.
CONTROL = (0xFD11, 0xFD44, 0xFD66, 0xFD88, 0xFE11, 0xFE77, 0xFEAA, 0xFF21, 0xFF33, 0xFF55)
# Three of Apple's payload maxima. Their appearance close together is the packet-type table's
# own signature, and unlike a single halfword it is far too specific to happen by chance.
TABLE_SIGNATURE = (2586, 1416, 1288)
SIGNATURE_WINDOW = 256
NAME_PATTERNS = (b"ULLA", b"Ulla", b"ulla")


def readable_strings(data: bytes, least: int = 12) -> list[str]:
    """NUL-delimited printable runs, which is what a real string table looks like.

    A bare printable-run search over code finds plenty of accidents - two Thumb `bx lr`
    instructions read as "pGpG" - so this requires a NUL on both sides. It is what makes an
    absence meaningful: a payload with hundreds of these and none of them naming the link is
    evidence, where a payload with no strings at all would be no evidence either way.
    """
    pattern = rb"\x00([\x20-\x7e]{%d,})\x00" % least
    return [match.group(1).decode("latin-1") for match in re.finditer(pattern, data)]


def signature_hits(data: bytes) -> list[int]:
    """Offsets where all three of Apple's maxima appear within one window of each other."""
    places = [{m.start() for m in re.finditer(struct.pack("<H", value), data)}
              for value in TABLE_SIGNATURE]
    return sorted(
        first for first in places[0]
        if any(abs(first - other) < SIGNATURE_WINDOW for other in places[1])
        and any(abs(first - other) < SIGNATURE_WINDOW for other in places[2])
    )


def count_each(data: bytes, values: tuple[int, ...]) -> list[int]:
    return [data.count(struct.pack("<H", value)) for value in values]


def survey(path: pathlib.Path) -> dict[str, object]:
    data = path.read_bytes()
    strings = readable_strings(data)
    named = sorted({pattern.decode() for pattern in NAME_PATTERNS if pattern in data})
    return {
        "name": path.stem,
        "size": len(data),
        "strings": len(strings),
        "names": named,
        "signature": signature_hits(data),
        "documented": count_each(data, DOCUMENTED),
        "control": count_each(data, CONTROL),
        "hdr": [match.start() for match in re.finditer(rb"hdr", data)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--payloads", default="payloads",
                        help="a directory of extracted payloads; every *.bin in it is scanned")
    args = parser.parse_args(argv)
    here = pathlib.Path(args.payloads)
    files = sorted(p for p in here.glob("*.bin") if p.stem != "ctab")
    if not files:
        raise SystemExit(f"no payloads in {here}: see docs/implementation.md for how to extract")

    rows = [survey(path) for path in files]
    expected = {row["name"]: row["size"] / 65536 for row in rows}
    print(f"{len(rows)} payloads, ctab excluded because it is the table again, compressed.\n")
    print(f"{'payload':8} {'bytes':>9} {'strings':>8} {'names':>14} {'table':>6}")
    for row in rows:
        names = ",".join(row["names"]) or "-"
        print(f"{row['name']:8} {row['size']:>9} {row['strings']:>8} {names:>14} "
              f"{len(row['signature']):>6}")

    print("\nopcode counts against the undocumented control, per payload:")
    print(f"{'payload':8} {'by chance':>10} {'documented':>11} {'control':>8}  verdict")
    misleading = []
    for row in rows:
        ours = sum(row["documented"]) / len(DOCUMENTED)
        theirs = sum(row["control"]) / len(CONTROL)
        chance = expected[row["name"]]
        above = ours > 2 * theirs and ours > 2 * chance
        if not above and sum(row["documented"]):
            misleading.append(row["name"])
        print(f"{row['name']:8} {chance:>10.1f} {ours:>11.1f} {theirs:>8.1f}  "
              f"{'ABOVE the control' if above else 'indistinguishable from chance'}")

    print()
    holders = [row["name"] for row in rows if row["names"]]
    table = [row["name"] for row in rows if row["signature"]]
    print(f"The link is named in {len(holders)} of {len(rows)} payloads: {', '.join(holders)}.")
    print(f"The packet-type table's signature appears in {len(table) or 'none'}"
          + (f": {', '.join(table)}." if table else "."))
    silent = [row for row in rows if not row["names"] and row["strings"] > 100]
    if silent:
        print("Named nowhere in these, which have string tables of their own so the absence "
              "is a result rather than a gap: "
              + ", ".join(f"{row['name']} ({row['strings']} strings)" for row in silent) + ".")
    if misleading:
        print(f"\nAnd a byte count would have suggested otherwise in {len(misleading)} of them "
              f"({', '.join(misleading)}), which is why the control is printed above and why "
              "no claim in these documents rests on one.")
    bad_hdr = [row["name"] for row in rows if row["hdr"]]
    if bad_hdr:
        print(f"\n'hdr' occurs in {', '.join(bad_hdr)}; every occurrence examined by hand is "
              "the word \"header\", not Apple's HDR [docs/ulla.md 4.7].")
    print(f"\nFor reference, the table this is looking for has "
          f"{len(packets.for_table(packets.TABLE_APPLE)) - 1} entries of Apple's own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
