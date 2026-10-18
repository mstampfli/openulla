#!/usr/bin/env python3
"""Find this protocol's frames in a Bluetooth capture and print what they say.

Reads a btsnoop file in either of the two layouts openulla/capture.py describes: the HCI UART
datalink that Android and some tools write, and the BlueZ monitor datalink that `btmon -w`
writes. It reports the HCI vendor commands and events of docs/protocol.md section 6 and the
AAP frames of section 2, finding the channel the accessory protocol runs on from the L2CAP
signalling itself.

    btmon -w /tmp/air.btsnoop          # in one terminal, as root
    python3 tools/aap_probe.py --device AA:BB:CC:DD:EE:FF --seq start   # in another
    python3 tools/capture_decode.py /tmp/air.btsnoop

With --cid you can name a channel id directly, for a capture that starts after the channel
was opened. --raw adds the bytes of each frame.

A capture holds Bluetooth addresses, and this project's rules keep captures off the internet:
this tool prints only the frames it recognises, and never the rest of the file.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import capture  # noqa: E402
from openulla.errors import ProtocolError  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="a btsnoop file")
    ap.add_argument("--cid", action="append", type=lambda s: int(s, 0), default=[],
                    help="also read this L2CAP channel id as the accessory protocol")
    ap.add_argument("--raw", action="store_true", help="print the bytes of each frame too")
    args = ap.parse_args()
    if not os.path.exists(args.capture):
        raise SystemExit(f"no such file: {args.capture}")
    try:
        found = list(capture.walk_file(args.capture, args.cid))
    except ProtocolError as exc:
        raise SystemExit(f"cannot read this capture: {exc}")

    for item in found:
        way = "->" if item.packet.outbound else "<-"
        print(f"#{item.packet.index} {way} {item.label}")
        for line in item.lines:
            print(f"    {line}")
        if args.raw:
            print(f"    raw {item.data.hex(' ')}")
    print(f"\n{len(found)} frames of this protocol in {os.path.basename(args.capture)}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
