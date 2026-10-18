#!/usr/bin/env python3
"""Decode one frame, PDU, command or event of this protocol from hex.

Useful when reading a capture or a log: it names the fields the way
docs/protocol.md does, and for the band messages it also says which bands a mask means.

    python3 tools/ulla_decode.py 04 00 04 00 36 00 00
    python3 tools/ulla_decode.py --lmp f6 17 e6
    python3 tools/ulla_decode.py --hci-event ff 07 00 0a 0b 00 01 00 ff
    echo 50 fe 07 0b 00 00 00 01 03 00 | python3 tools/ulla_decode.py

With no --kind the tool tries every decoder and prints each one that fits, which is also how
it tells you that a frame is not one of these. Hex may be spaced or not, and 0x prefixes and
colons are ignored.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import aap, bands, hci, lmp  # noqa: E402
from openulla.codes import HciError, SessionStatus, reason_name  # noqa: E402
from openulla.errors import ProtocolError  # noqa: E402

OPCODE_NAMES = {int(op): op.name for op in aap.Opcode}


def named(value, enum):
    try:
        return f"{enum(value).name} (0x{value:02x})"
    except ValueError:
        return f"0x{value:02x}"


def describe_bands(mask):
    out = []
    for index in bands.mask_bands(mask):
        band = bands.band(index)
        if band.defined:
            out.append(f"{index}: {band.start_mhz}-{band.end_mhz} MHz, {band.channels} channels")
        else:
            out.append(f"{index}: the empty record, which every path refuses")
    return out


def decode_aap(data):
    frame = aap.parse_frame(data)
    lines = [f"AAP frame: type {frame.ftype} ({aap.FrameType(frame.ftype).name.lower()}), "
             f"service {frame.service}"]
    if frame.ftype == aap.FrameType.OPEN:
        major = int.from_bytes(frame.body[0:2], "little")
        minor = int.from_bytes(frame.body[2:4], "little")
        lines.append(f"  open request, protocol version {major}.{minor}, "
                     f"{len(data)} bytes (sixteen are required)")
    elif frame.ftype == aap.FrameType.OPEN_REPLY:
        reply = aap.OpenReply.from_bytes(frame.body)
        lines.append(f"  open reply, status 0x{reply.status:02x}, "
                     f"accessory version {reply.major}.{reply.minor}")
    elif frame.ftype == aap.FrameType.DATA:
        opcode, body = aap.parse_payload(frame.body)
        lines.append(f"  opcode 0x{opcode:02x} "
                     f"({OPCODE_NAMES.get(opcode, 'not one this protocol uses')}), "
                     f"body {body.hex(' ') or 'empty'}")
        if opcode == aap.Opcode.START_SESSION and len(body) == 1:
            lines.append(f"  mode byte {body[0]}")
        elif opcode == aap.Opcode.STATUS:
            status = aap.parse_status(frame.body)
            lines.append(f"  status {named(status, SessionStatus)}")
        elif opcode == aap.Opcode.WRAPPER:
            for width in (4, 2):
                try:
                    wrapped = aap.parse_wrapper(frame.body, width)
                except ProtocolError:
                    continue
                kind = " (the ULLA report streams)" if wrapped.cls_ == aap.CLASS_ULLA_REPORTS else ""
                lines.append(f"  wrapper, {width}-byte class 0x{wrapped.cls_:08x}{kind}, "
                             f"payload {wrapped.body.hex(' ') or 'empty'}")
                break
    return lines


def decode_lmp(data):
    head = lmp.parse_header(data)
    lines = [f"LMP PDU: opcode 0x{head.opcode:02x}, transaction id {head.tid}"
             + (f", extended opcode 0x{head.ext_opcode:02x}" if head.ext_opcode is not None else "")]
    if head.ext_opcode is not None and head.opcode in (lmp.ESCAPE_7B, lmp.ESCAPE_7F):
        fate = "dispatched" if lmp.implemented(head.opcode, head.ext_opcode) else "refused"
        lines.append(f"  an earbud of build 9A348 has this one {fate}")
    parsed = lmp.parse(data)
    if isinstance(parsed, lmp.SetupRequest):
        lines += [
            "  the ULLA setup request",
            f"  radio value 0x{parsed.radio_value:08x} (read from the source's own hardware)",
            f"  configuration word 0x{parsed.config_word:04x}",
            f"  flags 0x{parsed.flags:02x}, further flags 0x{parsed.extra_flags:02x}",
        ]
    elif isinstance(parsed, lmp.Configuration):
        lines += [f"  the ULLA configuration: 0x{parsed.value32:08x}, 0x{parsed.value16:04x}"]
    elif isinstance(parsed, lmp.Accepted):
        lines += [f"  accepted, for opcode 0x{parsed.accepted_opcode:02x}"]
    elif isinstance(parsed, lmp.NotAccepted):
        lines += [f"  not accepted, for opcode 0x{parsed.rejected_opcode:02x}, "
                  f"reason {reason_name(parsed.reason)} (0x{parsed.reason:02x})"]
    elif isinstance(parsed, lmp.AllowedBands):
        lines += [f"  allowed-band mask 0x{parsed.mask:02x}"]
        lines += [f"    band {line}" for line in describe_bands(parsed.mask)]
    elif isinstance(parsed, lmp.BandRanges):
        lines += [f"  band ranges, count byte {parsed.count_byte}, "
                  f"{len(parsed.ranges)} triples read from the body length"]
        for r in parsed.ranges:
            lines.append(f"    band {r.band}, first channel {r.first_channel}, "
                         f"length {r.length}")
    return lines


def decode_hci_command(data):
    if len(data) < 3:
        raise ProtocolError("an HCI command is at least three bytes")
    opcode = int.from_bytes(data[0:2], "little")
    length, params = data[2], data[3:]
    if length != len(params):
        raise ProtocolError(f"the command says {length} parameter bytes, carries {len(params)}")
    ogf, ocf = opcode >> 10, opcode & 0x3FF
    lines = [f"HCI command: opcode 0x{opcode:04x} (group 0x{ogf:02x}, command 0x{ocf:03x}), "
             f"{length} parameter bytes"]
    if opcode == hci.OPCODE_ULLA_SETUP:
        setup = hci.UllaSetup.from_bytes(params)
        flags = (
            f"  flags 0x{setup.flags:02x} (bits 0, 1 and 3 reach the PDU)"
            if setup.mode == 0
            else f"  flags 0x{setup.flags:02x} (a value on this path, 0 to 4)"
        )
        lines += [
            "  the command that starts the transition",
            f"  connection handle 0x{setup.handle:04x}",
            f"  configuration word 0x{setup.config_word:04x}",
            f"{flags}, further flags 0x{setup.extra_flags:02x}",
            f"  mode {setup.mode}"
            + (" (the transition path)" if setup.mode == 0 else " (the procedure path)"),
        ]
        refusal = setup.refusal()
        if refusal is not None:
            lines.append(f"  the controller would refuse this with 0x{refusal:02x}")
    elif opcode in (hci.OPCODE_ALLOWED_BANDS, hci.OPCODE_ALLOWED_BANDS_AGAIN) and len(params) == 1:
        lines.append(f"  allowed-band mask 0x{params[0]:02x}")
        lines += [f"    band {line}" for line in describe_bands(params[0])]
    elif opcode == hci.OPCODE_SET_LINK_BAND and len(params) == 3:
        handle = int.from_bytes(params[0:2], "little")
        index = params[2]
        where = "none" if index == bands.NO_BAND else f"band {index}"
        lines.append(f"  handle 0x{handle:04x} is set to {where}")
    elif opcode == hci.OPCODE_COUNTRY_CODES and params:
        lines.append(f"  {params[0]} country codes: " + ", ".join(
            "unavailable" if c == 0xFF else str(c) for c in params[1:]))
    elif opcode == hci.OPCODE_BAND_GATE and len(params) == 1:
        lines.append("  the band command family is switched "
                     + ("on" if params[0] else "off"))
    return lines


def decode_hci_event(data):
    if len(data) < 2:
        raise ProtocolError("an HCI event is at least two bytes")
    code, length, params = data[0], data[1], data[2:]
    if length != len(params):
        raise ProtocolError(f"the event says {length} parameter bytes, carries {len(params)}")
    lines = [f"HCI event: code 0x{code:02x}, {length} parameter bytes"]
    if code == hci.EVENT_VENDOR and len(params) >= 2:
        ident = int.from_bytes(params[0:2], "little")
        lines.append(f"  vendor event id 0x{ident:04x}")
        if ident == hci.EVENT_ID_MODE_CHANGE:
            event = hci.ModeChangeEvent.from_bytes(data)
            lines += [f"  the ULLA mode change: {event.describe()}"]
        elif ident == hci.EVENT_ID_SECOND and len(params) == 5:
            handle = int.from_bytes(params[2:4], "little")
            lines.append(f"  the second ULLA event, handle 0x{handle:04x}, "
                         f"byte 0x{params[4]:02x}; what it reports is open")
    elif code == 0x0E and len(params) >= 4:  # command complete, for the vendor refusals
        opcode = int.from_bytes(params[1:3], "little")
        lines.append(f"  command complete for 0x{opcode:04x}, "
                     f"status {named(params[3], HciError) if params[3] else 'success (0x00)'}")
    return lines


DECODERS = {
    "aap": ("--aap", decode_aap),
    "lmp": ("--lmp", decode_lmp),
    "hci-command": ("--hci-command", decode_hci_command),
    "hci-event": ("--hci-event", decode_hci_event),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hex", nargs="*", help="the bytes, spaced or not")
    for kind, (flag, _) in DECODERS.items():
        ap.add_argument(flag, dest="kind", action="store_const", const=kind,
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
    kinds = [args.kind] if args.kind else list(DECODERS)
    fitted = []
    for kind in kinds:
        try:
            lines = DECODERS[kind][1](data)
        except (ProtocolError, ValueError, IndexError) as exc:
            if args.kind:
                raise SystemExit(f"not {kind}: {exc}")
            continue
        fitted.append(lines)
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
