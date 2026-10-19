#!/usr/bin/env python3
"""Ask the earbuds which of the Apple vendor codec's eight bytes they actually validate.

The open question this is for: the vendor A2DP endpoint advertises eight codec-specific bytes
whose layout is unknown, and reading them again cannot settle it, because a read returns what
the device offers rather than which parts of it the device checks (docs/ulla.md 4.6). Offering
a configuration back with one byte altered can settle it. AVDTP's reject names the service
category that failed and carries an error code, so a byte that can be changed freely is not a
field the device validates, and one that earns a reject is.

**Both outcomes are results.** If every altered byte is accepted, these bytes are not validated
at all, which is the same shape of answer as the mode byte of docs/measurements.md section 11.
If some are refused and others are not, the refused ones are fields.

Read-only by default: it discovers the endpoints and prints their capabilities. The probe that
offers configurations is printed as a dry run unless you pass --live, because a SetConfiguration
changes the sink's state and needs BlueZ's own media plugin out of the way, or it will be
competing for the same endpoint:

  sudo btmgmt ... or disable the audio plugin, then
  sudo python3 tools/a2dp_probe.py AA:BB:CC:DD:EE:FF --probe --live

All framing lives in `openulla/avdtp.py` and the bytes being probed in `openulla/a2dp.py`, so
this file is only the part that leaves the machine.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import a2dp, avdtp
from openulla.errors import ProtocolError
from openulla.transport import L2capTransport

MAC_LENGTH = 17


class Simulated:
    """Stands in for a real channel, answering from `openulla.avdtp.SimulatedSink`."""

    def __init__(self) -> None:
        self.sink = avdtp.SimulatedSink(a2dp.ENDPOINTS)

    def send(self, data: bytes) -> None:
        self.pending = self.sink.answer(data)

    def recv(self, timeout: float | None = None) -> bytes:
        return self.pending

    def close(self) -> None:
        return None


def exchange(link: L2capTransport | Simulated, message: bytes, timeout: float) -> bytes:
    link.send(message)
    return link.recv(timeout=timeout)


def describe_capabilities(capabilities: tuple[avdtp.Capability, ...]) -> list[str]:
    lines: list[str] = []
    for capability in capabilities:
        if capability.category == avdtp.CATEGORY_MEDIA_TRANSPORT:
            lines.append("    media transport")
            continue
        if capability.category != avdtp.CATEGORY_MEDIA_CODEC:
            lines.append(f"    category 0x{capability.category:02x}: "
                         f"{capability.payload.hex(' ') or 'empty'}")
            continue
        codec = capability.codec
        specific = capability.codec_specific
        lines.append(f"    media codec {a2dp.name(codec if codec is not None else -1)}: "
                     f"{specific.hex(' ')}")
        known = a2dp.ENDPOINTS.get(codec if codec is not None else -1)
        if known is not None:
            lines.append("      matches the firmware's own table"
                         if specific == known[1]
                         else f"      DIFFERS from the firmware, which has {known[1].hex(' ')}")
    return lines


def alterations(capability: bytes, first: int) -> list[tuple[int, bytes]]:
    """One alteration per byte from `first` on: flip its low bit, the smallest real change.

    The whole capability is offered, not just the part being altered. A vendor codec's
    codec-specific field is the four vendor bytes, then the two codec-id bytes, then the eight
    in question, and a configuration carrying only the last eight would be refused for being
    malformed rather than for the byte that changed, which would make every answer worthless.

    A smallest change is the right probe. A byte that is a bitfield may accept a large change in
    an unused bit and refuse a small one in a used bit, so the low bit is the least likely to be
    mistaken for "the device does not care about this byte at all".
    """
    out: list[tuple[int, bytes]] = []
    for index in range(first, len(capability)):
        altered = bytearray(capability)
        altered[index] ^= 0x01
        out.append((index - first, bytes(altered)))
    return out


def probe(link: L2capTransport | Simulated | None, seid: int, codec: int, capability: bytes,
          first: int, timeout: float) -> int:
    """Offer the whole capability unaltered, then with each byte from `first` on flipped."""
    refused = 0
    offers: list[tuple[str, bytes]] = [("unaltered", capability)]
    offers += [(f"byte {index} low bit flipped", altered)
               for index, altered in alterations(capability, first)]
    for what, specific in offers:
        message = avdtp.set_configuration(
            acp_seid=seid,
            int_seid=1,
            capabilities=(avdtp.MEDIA_TRANSPORT, avdtp.media_codec(codec, specific)),
        )
        if link is None:
            print(f"  would offer {what:<28} {specific.hex(' ')}")
            print(f"    as {message.hex(' ')}")
            continue
        reply = exchange(link, message, timeout)
        reject = avdtp.parse_set_configuration_response(reply)
        if reject is None:
            print(f"  {what:<28} ACCEPTED")
        else:
            refused += 1
            print(f"  {what:<28} refused: {reject}")
    return refused


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address", nargs="?",
                        help="the earbuds' Bluetooth address; not needed with --simulate")
    parser.add_argument("--probe", action="store_true",
                        help="offer configurations with one byte altered at a time")
    parser.add_argument("--live", action="store_true",
                        help="actually send them; without this --probe is a dry run")
    parser.add_argument("--simulate", action="store_true",
                        help="run against an in-process sink instead of hardware, which"
                             " exercises this tool's own path; its refusal policy is the"
                             " simulator's invention and says nothing about the earbuds")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    if not args.simulate and (args.address is None or len(args.address) != MAC_LENGTH):
        parser.error("an address looks like AA:BB:CC:DD:EE:FF, or pass --simulate")
    if args.live and not args.probe:
        parser.error("--live only means anything with --probe")

    vendor = a2dp.ENDPOINTS[0xFF]
    print(f"the vendor endpoint this is about: {a2dp.name(0xFF)}, {vendor[1].hex(' ')}")
    print(f"its eight codec-specific bytes: {a2dp.vendor_specific(vendor[1]).hex(' ')}")

    # The eight bytes in question start after the four vendor bytes and the two codec-id ones.
    first_probed = len(vendor[1]) - len(a2dp.vendor_specific(vendor[1]))

    if args.probe and not args.live:
        print("\ndry run, nothing is sent. The offers would be:")
        probe(None, seid=1, codec=0xFF, capability=vendor[1], first=first_probed, timeout=0.0)
        print("\nPass --live to send them, with BlueZ's media plugin out of the way.")
        return 0

    link: L2capTransport | Simulated
    if args.simulate:
        print("\nsimulated: the capability reads come from the firmware's own table, and the")
        print("refusals are this simulator's policy, not the earbuds'.")
        link = Simulated()
    else:
        try:
            link = L2capTransport.connect(args.address, psm=avdtp.PSM)
        except (OSError, ProtocolError) as exc:
            print(f"could not open the signalling channel: {exc}")
            return 1
    try:
        endpoints = avdtp.parse_discover(exchange(link, avdtp.discover(), args.timeout))
        print(f"\n{len(endpoints)} endpoints:")
        target: int | None = None
        for endpoint in endpoints:
            role = "sink" if endpoint.is_sink else "source"
            print(f"  SEID {endpoint.seid} {role}{', in use' if endpoint.in_use else ''}")
            reply = exchange(link, avdtp.get_capabilities(endpoint.seid, every=True),
                             args.timeout)
            head = avdtp.parse_header(reply)
            if head.message_type != avdtp.RESPONSE_ACCEPT:
                print("    refused the capability read")
                continue
            capabilities = avdtp.parse_capabilities(reply[2:])
            for line in describe_capabilities(capabilities):
                print(line)
            for capability in capabilities:
                if capability.codec == 0xFF:
                    target = endpoint.seid
        if not args.probe:
            return 0
        if target is None:
            print("\nno vendor endpoint was reported, so there is nothing to probe")
            return 1
        print(f"\noffering configurations to SEID {target}, one byte altered at a time:")
        refused = probe(link, target, 0xFF, vendor[1], first_probed, args.timeout)
        print(f"\n{refused} of nine offers refused.")
        print("Every offer accepted means these bytes are not validated, which is a result;"
              " a mixed answer names the fields.")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
