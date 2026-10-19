#!/usr/bin/env python3
"""Ask which payloads of a build hold this link's material, and which hold none of it.

Every firmware claim in these documents comes from three payloads - `acib`, `rkof` and `sbd1` -
and that could be where the looking happened rather than where the material is. This answers it
over whatever payloads you have extracted:

    python3 tools/payload_survey.py --payloads payloads

It scans every `*.bin` in the directory and **says what it scanned**. It does not claim to have
seen the build: a UARP SuperBinary of this device holds twenty payload records, three of which
are themselves containers, so what you get depends entirely on what you extracted.
docs/implementation.md has the commands and docs/ulla.md 4.7 the answer for build 9A348.

**The opcode counts are the part to be careful with, and an earlier version of this tool was
not.** A two-byte pattern turns up in a firmware image by chance, so counting a vendor opcode
in a megabyte payload finds it a dozen times or more whether or not that payload has ever heard
of it. But "by chance" is not uniform: these images are far from random, and in some of them
halfwords in the 0xFD00 range are several times commoner than a flat model predicts. So each
opcode is judged against **its own payload's distribution of every halfword in the same range**
and reported with that percentile, rather than against a handful of hand-picked controls whose
own level turned out to sit well above the median. Ten such controls are still printed, because
seeing them is what makes the point, but they decide nothing.

A count is never evidence that a payload knows an opcode. A handler in a dispatch table is,
which is how docs/protocol.md section 6 reaches every opcode it names.
"""
import argparse
import os
import pathlib
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import hci

# The six this project documents as reaching the link, the packet table or the band plan.
DOCUMENTED = {
    hci.OPCODE_PACKET_SIZE: "packet size",
    hci.OPCODE_PACKET_TABLE_DIRECT: "packet table",
    hci.OPCODE_ULLA_SETUP: "ulla setup",
    hci.OPCODE_STREAM_DESCRIPTOR: "stream descriptor",
    hci.OPCODE_SET_LINK_BAND: "set link band",
    hci.OPCODE_FEATURE_GROUP: "feature group",
}
# Shown for illustration only. They decide nothing: measured against each payload's own
# distribution these ten sit near its 62nd percentile rather than its median, so treating them
# as "what chance looks like" biases the answer, which is what the percentile below avoids.
CONTROL = (0xFD11, 0xFD44, 0xFD66, 0xFD88, 0xFE11, 0xFE77, 0xFEAA, 0xFF21, 0xFF33, 0xFF55)
RANGE = (0xFD00, 0x10000)  # the span the documented opcodes live in, and the comparison set
PERCENTILE = 99  # an opcode above its payload's own p99 is worth a second look, and no more
MINIMUM_COUNT = 2  # below this the comparison has no power, whatever the percentile says
# Three of Apple's payload maxima. Their appearance close together is the packet-type table's
# signature; a null model of random triples in the same numeric range satisfies it in about one
# per cent of draws, so it is specific, but it is not unique to one payload.
TABLE_SIGNATURE = (2586, 1416, 1288)
SIGNATURE_WINDOW = 256
# All sixteen case permutations, because "in any case" has to mean that.
NAME_PATTERNS = tuple(
    "".join(c.upper() if bit & (1 << i) else c for i, c in enumerate("ulla")).encode()
    for bit in range(16)
)


def readable_strings(data: bytes, least: int = 12) -> list[str]:
    """NUL-delimited printable runs, which is what a real string table looks like.

    A bare printable-run search over code finds plenty of accidents - two Thumb `bx lr`
    instructions read as "pGpG" - so this requires a NUL on both sides. The terminator is
    matched with a **lookahead**: consuming it, as the first version of this did, makes the
    next string's leading NUL unavailable and silently drops every second entry of a packed
    table. That understated six published figures by nearly half.
    """
    pattern = rb"\x00([\x20-\x7e]{%d,})(?=\x00)" % least
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


def halfword_counts(data: bytes) -> dict[int, int]:
    """How often each halfword of RANGE occurs, which is this payload's own null model."""
    counts = {value: 0 for value in range(*RANGE)}
    for at in range(0, len(data) - 1):
        value = struct.unpack_from("<H", data, at)[0]
        if value >= RANGE[0]:
            counts[value] += 1
    return counts


def notable(count: int, threshold: int) -> bool:
    """Whether a count stands out against its payload's own distribution.

    Two guards, and the first version of this had neither. It asked what fraction of halfwords
    occur no more often than this one, which in a small payload - where nearly every halfword
    occurs zero times - is about a hundred per cent for a count of **zero**, so it flagged
    nineteen files for all six opcodes on the strength of not containing them. A count has to
    exceed the threshold, and it has to be more than a single stray pair of bytes, or the test
    has no power to begin with.
    """
    return count > threshold and count >= MINIMUM_COUNT


def survey(path: pathlib.Path) -> dict[str, object]:
    data = path.read_bytes()
    if not data:
        return {"name": path.stem, "size": 0, "empty": True}
    counts = halfword_counts(data)
    threshold = sorted(counts.values())[int(len(counts) * PERCENTILE / 100) - 1]
    return {
        "name": path.stem,
        "size": len(data),
        "empty": False,
        "strings": len(readable_strings(data)),
        "names": sorted({p.decode() for p in NAME_PATTERNS if p in data}),
        "signature": signature_hits(data),
        "riff": data[:4] == b"RIFF",
        "counts": {op: counts[op] for op in DOCUMENTED},
        "control_max": max(counts[c] for c in CONTROL),
        "threshold": threshold,
        "hdr": sorted({m.group().decode() for m in re.finditer(rb"(?i)hdr", data)}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--payloads", default="payloads",
                        help="a directory of extracted payloads; every *.bin in it is scanned")
    args = parser.parse_args(argv)
    here = pathlib.Path(args.payloads)
    files = sorted(here.glob("*.bin"))
    if not files:
        raise SystemExit(f"no payloads in {here}: see docs/implementation.md for how to extract")

    rows = [survey(path) for path in files]
    live = [row for row in rows if not row["empty"]]
    empty = [row["name"] for row in rows if row["empty"]]
    print(f"{len(files)} files scanned, {len(live)} with any content"
          + (f"; empty and so carrying nothing: {', '.join(empty)}" if empty else "")
          + f". This is what was in {here}, not necessarily a whole build.\n")

    print(f"{'payload':8} {'bytes':>9} {'strings':>8} {'names':>14} {'table':>6} {'riff':>5}")
    for row in live:
        names = ",".join(row["names"]) or "-"
        print(f"{row['name']:8} {row['size']:>9} {row['strings']:>8} {names:>14} "
              f"{len(row['signature']):>6} {'yes' if row['riff'] else '':>5}")

    print(f"\nEvery documented opcode's own count, with where it sits in its payload's own"
          f" distribution of halfwords from {RANGE[0]:#x} up. The p{PERCENTILE} column is that"
          f" payload's own threshold, and the control column is the busiest of ten"
          f" undocumented halfwords, shown to be looked at and not to decide anything.")
    print(f"\n{'payload':8} " + " ".join(f"{op:#06x}" for op in DOCUMENTED)
          + f" {'p' + str(PERCENTILE):>5} {'ctrl':>5}  over p{PERCENTILE}")
    standouts = []
    for row in live:
        cells = " ".join(f"{row['counts'][op]:>6}" for op in DOCUMENTED)
        over = [op for op in DOCUMENTED if notable(row["counts"][op], row["threshold"])]
        if over:
            standouts.append((row["name"], over))
        print(f"{row['name']:8} {cells} {row['threshold']:>5} {row['control_max']:>5}  "
              + (", ".join(f"{op:#06x}" for op in over) if over else "-"))

    print()
    holders = [row["name"] for row in live if row["names"]]
    table = [row["name"] for row in live if row["signature"]]
    riff = [row["name"] for row in live if row["riff"]]
    print(f"The link is named in {len(holders)} of the {len(live)} non-empty files: "
          f"{', '.join(holders) or 'none'}.")
    print(f"The packet-type table's signature appears in {len(table) or 'none'}"
          + (f": {', '.join(table)}." if table else "."))
    if riff:
        print(f"{len(riff)} of them are RIFF audio files, which implement no vendor command: "
              f"{', '.join(riff)}.")
    silent = [row for row in live if not row["names"] and row["strings"] > 100]
    if silent:
        print("Named nowhere in these, which have string tables of their own so the absence is "
              "a result rather than a gap: "
              + ", ".join(f"{row['name']} ({row['strings']})" for row in silent) + ".")
    if standouts:
        print(f"\nAbove its own p{PERCENTILE} in {len(standouts)}: "
              + "; ".join(f"{name} ({', '.join(f'{op:#06x}' for op in ops)})"
                          for name, ops in standouts)
              + ". That is a reason to look, not a finding: what settles whether a payload "
                "knows an opcode is a handler in a dispatch table.")
    else:
        print(f"\nNo documented opcode exceeds its own payload's p{PERCENTILE} anywhere here, "
              "so no byte count in this directory points at a payload knowing one.")
    beats = [row for row in live
             if any(row["counts"][op] > row["control_max"] for op in DOCUMENTED)]
    if beats:
        audio = [row["name"] for row in beats if row["riff"]]
        print(f"\nFor contrast, a documented opcode outscores all ten illustrative controls in "
              f"{len(beats)} of them ({', '.join(row['name'] for row in beats)})"
              + (f" - including the audio files {', '.join(audio)} -" if audio else " -")
              + " which is why those controls are not the instrument.")
    hdr = {row["name"]: row["hdr"] for row in live if row["hdr"]}
    if hdr:
        print(f"\n'hdr' occurs in {len(hdr)} files, in these spellings: "
              + "; ".join(f"{name}: {', '.join(forms)}" for name, forms in hdr.items())
              + ". Each has to be read in context - docs/ulla.md 4.7 records what they were.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
