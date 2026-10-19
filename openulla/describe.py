"""Human-readable decodes of the frames, PDUs, commands and events this package knows.

One place for the wording, so the hex decoder, the capture reader and anything else print the
same thing. Every statement here is the one the reference makes; the citations are in the
modules these functions call.
"""
from __future__ import annotations

from enum import IntEnum

from . import aap, bands, hci, lmp, packets, sibling
from .codes import HciError, SessionStatus, reason_name
from .errors import ProtocolError

OPCODE_NAMES = {int(op): op.name for op in aap.Opcode}
EVENT_COMMAND_COMPLETE = 0x0E
# The four of 7.1, named because a reader of a capture wants them named.
STREAM_COMMAND_NAMES = {
    hci.OPCODE_STREAM_DMA_ADDRESS: "the audio DMA address the start path requires",
    hci.OPCODE_STREAM_START: "start",
    hci.OPCODE_STREAM_STOP: "stop",
    hci.OPCODE_STREAM_DESCRIPTOR: "read the four-word descriptor back",
}
# Apple's six mask bits as one number, which is the immediate 0xFDEE writes.
APPLE_SIX_BITS = sum(1 << bit for bit in packets.APPLE_MASK_BITS)
# The other three writers of the maximum packet size (6.8). A capture reader wants these named
# because who owns that field is not settled, so any of them appearing is worth noticing.
SIZE_WRITER_NAMES = {
    hci.OPCODE_PACKET_SIZE_HALFWORD: (
        "also writes the maximum packet size, from its own request, behind a state test"
    ),
    hci.OPCODE_PACKET_SIZE_HALFWORD_ALT: (
        "also writes the maximum packet size, from its own request, except that 0xFFFF there "
        "means leave it unchanged"
    ),
    hci.OPCODE_PACKET_SIZE_INTERNAL: (
        "reaches an internal path that writes a hard-coded 1024 to the maximum packet size"
    ),
}


def named(value: int, enum: type[IntEnum]) -> str:
    try:
        return f"{enum(value).name} (0x{value:02x})"
    except ValueError:
        return f"0x{value:02x}"


def describe_bands(mask: int) -> list[str]:
    """One line per band a mask names, with its span."""
    out = []
    for index in bands.mask_bands(mask):
        band = bands.band(index)
        if band.defined:
            out.append(f"{index}: {band.start_mhz}-{band.end_mhz} MHz, {band.channels} channels")
        else:
            out.append(f"{index}: the empty record, which every path refuses")
    return out


FRAME_TYPE_NAMES = {int(t): t.name.lower() for t in aap.FrameType}


def describe_aap(data: bytes) -> list[str]:
    frame = aap.parse_frame(data)
    lines = [
        f"AAP frame: type {frame.ftype} "
        f"({FRAME_TYPE_NAMES.get(frame.ftype, 'not a type this protocol uses')}), "
        f"service {frame.service}"
    ]
    if frame.ftype == aap.FrameType.OPEN:
        major = int.from_bytes(frame.body[0:2], "little")
        minor = int.from_bytes(frame.body[2:4], "little")
        lines.append(
            f"  open request, protocol version {major}.{minor}, "
            f"{len(data)} bytes (sixteen are required)"
        )
    elif frame.ftype == aap.FrameType.OPEN_REPLY:
        reply = aap.OpenReply.from_bytes(frame.body)
        lines.append(
            f"  open reply, status 0x{reply.status:02x}, "
            f"accessory version {reply.major}.{reply.minor}"
        )
    elif frame.ftype == aap.FrameType.DATA:
        opcode, body = aap.parse_payload(frame.body)
        lines.append(
            f"  opcode 0x{opcode:02x} "
            f"({OPCODE_NAMES.get(opcode, 'not one this protocol uses')}), "
            f"body {body.hex(' ') or 'empty'}"
        )
        if opcode == aap.Opcode.START_SESSION and len(body) == 1:
            lines.append(f"  mode byte {body[0]}")
        elif opcode == aap.Opcode.STATUS:
            lines.append(f"  status {named(aap.parse_status(frame.body), SessionStatus)}")
        elif opcode == aap.Opcode.WRAPPER:
            for width in (4, 2):
                try:
                    wrapped = aap.parse_wrapper(frame.body, width)
                except ProtocolError:
                    continue
                kind = (
                    " (the ULLA report streams)"
                    if wrapped.cls_ == aap.CLASS_ULLA_REPORTS
                    else ""
                )
                lines.append(
                    f"  wrapper, {width}-byte class 0x{wrapped.cls_:08x}{kind}, "
                    f"payload {wrapped.body.hex(' ') or 'empty'}"
                )
                break
    return lines


def describe_lmp(data: bytes) -> list[str]:
    head = lmp.parse_header(data)
    lines = [
        f"LMP PDU: opcode 0x{head.opcode:02x}, transaction id {head.tid}"
        + (f", extended opcode 0x{head.ext_opcode:02x}" if head.ext_opcode is not None else "")
    ]
    if head.ext_opcode is not None and head.opcode in (lmp.ESCAPE_7B, lmp.ESCAPE_7F):
        fate = "dispatched" if lmp.implemented(head.opcode, head.ext_opcode) else "refused"
        lines.append(f"  an earbud of build 9A348 has this one {fate}")
    try:
        parsed: object = lmp.parse(data)
    except ProtocolError:
        # Worth reporting rather than refusing: a capture of a real link carries messages
        # this package does not decode, and the header above is already useful.
        lines.append(f"  body {data[head.body_offset:].hex(' ') or 'empty'}, not one this"
                     " package decodes; docs/protocol.md 5.6 lists what exists")
        return lines
    if isinstance(parsed, lmp.SetupRequest):
        lines += [
            "  the ULLA setup request",
            f"  radio value 0x{parsed.radio_value:08x} (read from the source's own hardware)",
            f"  configuration word 0x{parsed.config_word:04x}",
            f"  flags 0x{parsed.flags:02x}, further flags 0x{parsed.extra_flags:02x}",
        ]
    elif isinstance(parsed, lmp.Configuration):
        which = "0x74" if parsed.opcode == lmp.OPCODE_ULLA_CONFIG else "0x75"
        lines += [
            f"  the ULLA configuration ({which}): 0x{parsed.value32:08x}, 0x{parsed.value16:04x}"
        ]
        if parsed.would_be_dropped():
            lines.append(
                f"    0x75's receiver drops a halfword above "
                f"{lmp.Configuration.MAX_VALUE16_FOR_75}"
            )
    elif isinstance(parsed, lmp.SessionBare):
        lines += ["  the bare session message (0x76), header and nothing else"]
    elif isinstance(parsed, lmp.Accepted):
        lines += [f"  accepted, for opcode 0x{parsed.accepted_opcode:02x}"]
    elif isinstance(parsed, lmp.NotAccepted):
        lines += [
            f"  not accepted, for opcode 0x{parsed.rejected_opcode:02x}, "
            f"reason {reason_name(parsed.reason)} (0x{parsed.reason:02x})"
        ]
    elif isinstance(parsed, lmp.Features):
        kind = "reply" if parsed.reply else "request"
        lines += [
            f"  the feature exchange, {kind}: word 0x{parsed.word:08x}",
            f"    bit {lmp.FEATURE_BIT_BANDS} (the band family) is "
            + ("set" if parsed.word & (1 << lmp.FEATURE_BIT_BANDS) else "clear"),
            f"    bit {lmp.FEATURE_BIT_MODE_PROCEDURE} (the mode procedure) is "
            + ("set" if parsed.word & (1 << lmp.FEATURE_BIT_MODE_PROCEDURE) else "clear"),
            "    a feature is usable only where both ends' words carry its bit",
        ]
    elif isinstance(parsed, lmp.PacketPermission):
        allowed = parsed.allows()
        extra = [p.name for p in allowed if p.mask_bit in packets.APPLE_MASK_BITS]
        lines += [
            f"  the packet permission (0x7B/0x0C): 0x{parsed.permission:02x}, "
            f"{parsed.signed()} read signed as the firmware's own log does",
            "    it is applied only while the link's packet-type table is 2, protocol.md 5.14",
            f"    bits {lmp.PacketPermission.MASK_BITS} leave "
            + (", ".join(extra) if extra else "none of Apple's six packet types")
            + " selectable",
        ]
    elif isinstance(parsed, lmp.LinkHalfword):
        lines += [
            "  the halfword pair (0x7B/0x%02x): value 0x%04x"
            % (parsed.ext_opcode, parsed.value),
            "    the " + ("answer" if parsed.reply else "request")
            + ", kept at the link's +0x62; what it means is open, protocol.md 5.15",
        ]
        if parsed.reply and parsed.value == lmp.LinkHalfword.SEEN_IN_REPLY:
            lines += ["    this is the one value the firmware is seen putting in the answer"]
    elif isinstance(parsed, lmp.BandBlock):
        lines += [
            "  the counted band block (0x7B/0x%02x): %d bytes"
            % (parsed.ext_opcode, len(parsed.block)),
            "    the " + ("answer" if parsed.reply else "request")
            + f", gated on the band feature bit {lmp.BandBlock.FEATURE_BIT}, protocol.md 5.15",
        ]
        if not parsed.block:
            lines += ["    the block is empty"]
        for item_type, item_value in parsed.items():
            where: int | None = lmp.BandBlock.ITEM_FIELDS.get(item_type)
            shown: str = item_value.hex(" ") if item_value else "-"
            lines += [
                f"    item type 0x{item_type:X}, {len(item_value)} bytes: {shown}"
                + (f", to the link's +0x{where:02x}" if where is not None
                   else ", a type this firmware skips by its own length"),
            ]
    elif isinstance(parsed, lmp.PacketTableRequest):
        selectable = parsed.selectable()
        names = {0: "the basic-rate packets", 1: "the EDR packets"}
        lines += [
            f"  the packet-type table request (0x7B/0x0D): table {parsed.table}",
            "    " + names.get(
                parsed.table,
                "Apple's table, the only one that can select the six packet types of 5.13",
            ),
            f"    {len(selectable)} packet types become selectable, the largest carrying "
            f"{max(p.payload for p in selectable)} bytes",
            "    the specification's own form of this request, 0x7F/0x0B, bounds the table "
            "to 1",
        ]
    elif isinstance(parsed, lmp.AllowedBands):
        lines += [f"  allowed-band mask 0x{parsed.mask:02x}"]
        lines += [f"    band {line}" for line in describe_bands(parsed.mask)]
    elif isinstance(parsed, lmp.SelectorRequest):
        lines += [
            f"  the selector request (0x7B/0x0E): selector {parsed.selector}",
            "    its receiver answers with a value and a four-bit code, protocol.md 5.11",
        ]
    elif isinstance(parsed, lmp.ValueAndCode):
        where = parsed.code_class()
        same = parsed.uniform()
        lines += [
            f"  a value and a code (0x7B/0x0F): value 0x{parsed.value:02x}, code {parsed.code}",
            "    Apple's form of the power control response, protocol.md 5.14",
            "    its five two-bit fields: "
            + ", ".join(str(field) for field in parsed.fields())
            + (f"; all {same}" if same is not None else "; they differ"),
            "    the three pairs this firmware answers with are "
            + ", ".join(f"0x{v:02x}/{c}" for v, c in lmp.ValueAndCode.PAIRS),
            "    the receiver's masks put this code in "
            + (f"class {where}" if where else "neither class"),
        ]
    elif isinstance(parsed, lmp.BandRanges):
        lines += [
            f"  band ranges, {len(parsed.ranges)} triples, "
            "the count coming from the length and not from a field"
        ]
        if parsed.ignored:
            lines.append(f"    trailing bytes the division ignores: {parsed.ignored.hex(' ')}")
        for r in parsed.ranges:
            lines.append(
                f"    band {r.band}, first channel {r.first_channel}, length {r.length}"
            )
    return lines


def describe_hci_command(data: bytes) -> list[str]:
    if len(data) < 3:
        raise ProtocolError("an HCI command is at least three bytes")
    opcode = int.from_bytes(data[0:2], "little")
    length, params = data[2], data[3:]
    if length != len(params):
        raise ProtocolError(f"the command says {length} parameter bytes, carries {len(params)}")
    ogf, ocf = opcode >> 10, opcode & 0x3FF
    lines = [
        f"HCI command: opcode 0x{opcode:04x} (group 0x{ogf:02x}, command 0x{ocf:03x}), "
        f"{length} parameter bytes"
    ]
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
    elif opcode == hci.OPCODE_ULLA_SECOND and len(params) == 4:
        handle = int.from_bytes(params[0:2], "little")
        lines.append(f"  the second ULLA command: handle 0x{handle:04x}, mode {params[3]}")
    elif opcode in (hci.OPCODE_ALLOWED_BANDS, hci.OPCODE_ALLOWED_BANDS_AGAIN) and len(params) == 1:
        lines.append(f"  allowed-band mask 0x{params[0]:02x}")
        lines += [f"    band {line}" for line in describe_bands(params[0])]
    elif opcode == hci.OPCODE_SET_LINK_BAND and len(params) == 3:
        handle = int.from_bytes(params[0:2], "little")
        where = "none" if params[2] == bands.NO_BAND else f"band {params[2]}"
        lines.append(f"  handle 0x{handle:04x} is set to {where}")
    elif opcode == hci.OPCODE_COUNTRY_CODES and params:
        lines.append(
            f"  {params[0]} country codes: "
            + ", ".join("unavailable" if c == 0xFF else str(c) for c in params[1:])
        )
    elif opcode == hci.OPCODE_BAND_GATE and len(params) == 1:
        lines.append(
            "  the band command family is switched " + ("on" if params[0] else "off")
        )
    elif opcode == hci.OPCODE_FEATURE_GROUP and len(params) == 1:
        lines.append(
            "  the feature group that carries the mode procedure is switched "
            + ("on" if params[0] else "off")
        )
    elif opcode == hci.OPCODE_PACKET_SIZE and len(params) > hci.PACKET_SIZE_MODE_FIELD:
        mode = params[hci.PACKET_SIZE_MODE_FIELD]
        wide = mode == hci.PACKET_SIZE_MODE_WIDE
        size = hci.PACKET_SIZE_WIDE if wide else hci.PACKET_SIZE_BASIC
        buffer = hci.PACKET_SIZE_WIDE_BUFFER if wide else hci.PACKET_SIZE_BASIC_BUFFER
        lines += [
            "  the maximum packet size (protocol.md 6.8)",
            f"  mode {mode} selects {size} bytes, with {buffer} as the companion figure",
            "  " + (
                f"{size} is the five-slot payload of Apple's own packet-type table, so this "
                "host is configuring for HDR"
                if wide
                else f"{size} is DH5's payload maximum, the largest standard packet"
            ),
        ]
    elif opcode == hci.OPCODE_PACKET_TABLE_DIRECT and len(params) > hci.PACKET_TABLE_FLAG_FIELD:
        handle = int.from_bytes(params[hci.PACKET_TABLE_HANDLE_FIELD:][:2], "little")
        flag = params[hci.PACKET_TABLE_FLAG_FIELD]
        lines.append("  the direct route into Apple's packet-type table (protocol.md 6.9)")
        lines.append(f"  handle 0x{handle:04x}, flag byte 0x{flag:02x}")
        if flag:
            lines += [
                f"  permission 0x{hci.PACKET_TABLE_PERMISSION_SET:02x}, which leaves all six of "
                "Apple's packet types selectable",
                f"  the link's requested mask becomes 0x{APPLE_SIX_BITS:x} and its active table "
                f"becomes {packets.TABLE_APPLE}, with no negotiation",
            ]
        else:
            lines.append(
                f"  permission 0x{hci.PACKET_TABLE_PERMISSION_CLEAR:02x} on the standard byte "
                "instead, so this does not reach Apple's table"
            )
        if flag & 1:
            lines.append(
                "  bit 0 is set, which also forces the table-2 branch of the mask filter for "
                "EVERY link, not only this one"
            )
        if len(params) > hci.PACKET_TABLE_UNREAD_FIELD:
            lines.append(
                f"  the handler also reads two later bytes whose meaning is not established, "
                f"and never reads parameter {hci.PACKET_TABLE_UNREAD_FIELD}"
            )
    if opcode in SIZE_WRITER_NAMES:
        lines.append(f"  {SIZE_WRITER_NAMES[opcode]}")
    if opcode in STREAM_COMMAND_NAMES:
        lines.append(f"  the stream's control path: {STREAM_COMMAND_NAMES[opcode]}")
    elif opcode in hci.ULLA_SESSION_COMMANDS and opcode != hci.OPCODE_ULLA_SETUP:
        lines.append(
            f"  one of the eleven commands that reach the ULLA session, handler acib "
            f"0x{hci.ULLA_SESSION_COMMANDS[opcode]:x}"
        )
    return lines


def describe_hci_event(data: bytes) -> list[str]:
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
            lines.append(f"  the ULLA mode change: {event.describe()}")
        elif ident == hci.EVENT_ID_SECOND and len(params) == 5:
            handle = int.from_bytes(params[2:4], "little")
            lines.append(
                f"  the second ULLA event, handle 0x{handle:04x}, "
                f"byte 0x{params[4]:02x}; what it reports is open"
            )
    elif code == EVENT_COMMAND_COMPLETE and len(params) >= 4:
        opcode = int.from_bytes(params[1:3], "little")
        lines.append(
            f"  command complete for 0x{opcode:04x}, "
            f"status {named(params[3], HciError) if params[3] else 'success (0x00)'}"
        )
    return lines


def describe_sibling(data: bytes) -> list[str]:
    """One earbud-to-earbud frame, which rides AAP service 0x10 as client 5. [4]"""
    parsed = sibling.parse_frame(data)
    lines = [
        f"sibling frame: client {parsed.client}"
        f"{' (the session manager)' if parsed.client == sibling.CLIENT_SESSION_MANAGER else ''}"
        f", message {named(parsed.message, sibling.Message)}, "
        f"body {parsed.body.hex(' ') or 'empty'}"
    ]
    if parsed.message in sibling.EMPTY_BODY and parsed.body:
        lines.append("  this message carries no body in the firmware, so these bytes are extra")
    if parsed.message == sibling.Message.START_SESSION_REQ:
        request = sibling.parse_start_session_request(data)
        lines.append(
            f"  start session for {request.address.hex(':')}, mode {request.mode}; "
            "the six address bytes travel most significant first"
        )
    return lines


DECODERS = {
    "aap": describe_aap,
    "sibling": describe_sibling,
    "lmp": describe_lmp,
    "hci-command": describe_hci_command,
    "hci-event": describe_hci_event,
}
