#!/usr/bin/env python3
"""Decode one frame, PDU, command or event of this protocol from hex.

Useful when reading a capture or a log: it names the fields the way
docs/protocol.md does, and for the band messages it also says which bands a mask means. The
wording lives in openulla/describe.py, which tools/capture_decode.py prints as well.

    python3 tools/ulla_decode.py 04 00 04 00 36 00 00
    python3 tools/ulla_decode.py --lmp f6 17 e6
    python3 tools/ulla_decode.py --hci-event ff 07 00 0a 0b 00 01 00 ff
    echo 50 fe 07 0b 00 00 00 01 03 00 | python3 tools/ulla_decode.py

With no --kind the tool tries every decoder and prints each one that recognises something,
which is also how it tells you that a frame is not one of these. Hex may be spaced or not,
and 0x prefixes, colons and commas are ignored.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla.describe import DECODERS  # noqa: E402
from openulla.errors import ProtocolError  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hex", nargs="*", help="the bytes, spaced or not")
    for kind in DECODERS:
        ap.add_argument(f"--{kind}", dest="kind", action="store_const", const=kind,
                        help=f"decode as {kind}")
    args = ap.parse_args()
    text = " ".join(args.hex) if args.hex else sys.stdin.read()
    cleaned = text.replace("0x", "").replace(":", " ").replace(",", " ").split()
    try:
        data = bytes.fromhex("".join(cleaned))
    except ValueError as exc:
        raise SystemExit(f"not hex: {exc}")
    if not data:
        raise SystemExit("no bytes given")
    print(f"{len(data)} bytes: {data.hex(' ')}")

    fitted = []
    for kind in [args.kind] if args.kind else list(DECODERS):
        try:
            fitted.append(DECODERS[kind](data))
        except (ProtocolError, ValueError, IndexError) as exc:
            if args.kind:
                raise SystemExit(f"not {kind}: {exc}")
    # A short frame parses as several things at once, so when a decoder recognised an actual
    # message, only those are worth printing; the bare headers are noise.
    recognised = [lines for lines in fitted if len(lines) > 1]
    for lines in recognised or fitted:
        print()
        for line in lines:
            print(line)
    if not fitted:
        print("\nnone of the decoders fits these bytes")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
