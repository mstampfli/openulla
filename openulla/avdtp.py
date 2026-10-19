"""AVDTP signalling, enough of it to ask an endpoint what it will accept.

This exists for one open question. The Apple vendor A2DP endpoint advertises eight
codec-specific bytes whose layout is unknown ([a2dp.py](a2dp.py), docs/ulla.md 4.6), and
reading them again cannot settle it: a read returns what the device offers, not which parts of
it the device checks. What can settle it is **offering a configuration back with one field
altered and seeing whether it is refused**, because AVDTP's reject names the service category
that failed and carries an error code. A byte that can be changed freely is not a field the
device validates; one that earns a reject is.

Only the signalling this needs is here: the header, the capability list, Discover,
GetCapabilities, GetAllCapabilities and SetConfiguration. The layouts are the specification's
(AVDTP 1.3 section 8 for the header and the signals, A2DP for the media codec capability), not
this project's, and nothing here is read out of Apple's firmware.

The media transport and media codec categories are the two an A2DP configuration needs, so a
configuration built here is the minimum a sink would accept if it accepts anything at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .errors import ProtocolError

PSM = 0x0019  # the AVDTP signalling channel

# Message types, in the low two bits of the first header byte.
COMMAND = 0
GENERAL_REJECT = 1
RESPONSE_ACCEPT = 2
RESPONSE_REJECT = 3

SINGLE_PACKET = 0  # the packet type this module builds and the only one it parses

# Signal identifiers, in the low six bits of the second header byte.
SIG_DISCOVER = 0x01
SIG_GET_CAPABILITIES = 0x02
SIG_SET_CONFIGURATION = 0x03
SIG_GET_ALL_CAPABILITIES = 0x0C

# Service categories.
CATEGORY_MEDIA_TRANSPORT = 0x01
CATEGORY_MEDIA_CODEC = 0x07

MEDIA_TYPE_AUDIO = 0x00  # in the high nibble of the media codec capability's first byte

# Error codes this tool can actually provoke, from the specifications. Anything else is printed
# as a number rather than guessed at.
ERROR_NAMES: dict[int, str] = {
    0x12: "BAD_ACP_SEID",
    0x13: "SEP_IN_USE",
    0x17: "BAD_SERV_CATEGORY",
    0x18: "BAD_PAYLOAD_FORMAT",
    0x19: "NOT_SUPPORTED_COMMAND",
    0x1A: "INVALID_CAPABILITIES",
    0x22: "BAD_STATE",
    0xC1: "INVALID_CODEC_TYPE",
    0xC2: "NOT_SUPPORTED_CODEC_TYPE",
    0xE0: "INVALID_CODEC_PARAMETER",
    0xE1: "NOT_SUPPORTED_CODEC_PARAMETER",
}


def error_name(code: int) -> str:
    """The specification's name for an error code, or the number when it is not one of these."""
    return ERROR_NAMES.get(code, f"error 0x{code:02x}")


@dataclass(frozen=True)
class Header:
    label: int
    message_type: int
    signal: int


def header(label: int, signal: int, message_type: int = COMMAND) -> bytes:
    """The two-byte single-packet signalling header."""
    if not 0 <= label <= 0x0F:
        raise ProtocolError("the transaction label is four bits")
    if not 0 <= signal <= 0x3F:
        raise ProtocolError("the signal identifier is six bits")
    if not 0 <= message_type <= 3:
        raise ProtocolError("the message type is two bits")
    return bytes(((label << 4) | (SINGLE_PACKET << 2) | message_type, signal))


def parse_header(data: bytes) -> Header:
    if len(data) < 2:
        raise ProtocolError("a signalling message is at least two bytes")
    if (data[0] >> 2) & 3 != SINGLE_PACKET:
        raise ProtocolError("this module only parses single-packet messages")
    return Header(label=data[0] >> 4, message_type=data[0] & 3, signal=data[1] & 0x3F)


@dataclass(frozen=True)
class Endpoint:
    """One entry of a Discover response."""

    seid: int
    in_use: bool
    is_sink: bool


def discover(label: int = 0) -> bytes:
    return header(label, SIG_DISCOVER)


def parse_discover(data: bytes) -> tuple[Endpoint, ...]:
    body = data[2:]
    if len(body) % 2:
        raise ProtocolError("a Discover response is two bytes per endpoint")
    return tuple(
        Endpoint(seid=body[i] >> 2, in_use=bool(body[i] & 2), is_sink=bool(body[i + 1] & 8))
        for i in range(0, len(body), 2)
    )


def get_capabilities(seid: int, label: int = 0, every: bool = False) -> bytes:
    """Ask one endpoint what it offers. `every` sends GetAllCapabilities instead."""
    if not 0 <= seid <= 0x3F:
        raise ProtocolError("a SEID is six bits")
    signal = SIG_GET_ALL_CAPABILITIES if every else SIG_GET_CAPABILITIES
    return header(label, signal) + bytes((seid << 2,))


@dataclass(frozen=True)
class Capability:
    """One service capability: a category, and the bytes behind it."""

    category: int
    payload: bytes

    MEDIA_CODEC_FIXED: ClassVar[int] = 2  # the media type and codec type bytes

    def to_bytes(self) -> bytes:
        if len(self.payload) > 0xFF:
            raise ProtocolError("a capability's length is one byte")
        return bytes((self.category, len(self.payload))) + self.payload

    @property
    def codec(self) -> int | None:
        """The codec type, when this is a media codec capability."""
        if self.category != CATEGORY_MEDIA_CODEC or len(self.payload) < self.MEDIA_CODEC_FIXED:
            return None
        return self.payload[1]

    @property
    def codec_specific(self) -> bytes:
        """The codec-specific bytes, which is what the open question is about."""
        if self.category != CATEGORY_MEDIA_CODEC:
            raise ProtocolError("only a media codec capability has codec-specific bytes")
        return self.payload[self.MEDIA_CODEC_FIXED:]


def media_codec(codec: int, codec_specific: bytes) -> Capability:
    """Build a media codec capability for audio."""
    if not 0 <= codec <= 0xFF:
        raise ProtocolError("the codec type is one byte")
    return Capability(
        category=CATEGORY_MEDIA_CODEC,
        payload=bytes((MEDIA_TYPE_AUDIO << 4, codec)) + codec_specific,
    )


MEDIA_TRANSPORT = Capability(category=CATEGORY_MEDIA_TRANSPORT, payload=b"")


def parse_capabilities(data: bytes) -> tuple[Capability, ...]:
    """Walk a capability list, which is category, length, payload, repeated."""
    out: list[Capability] = []
    at = 0
    while at + 1 < len(data):
        category, length = data[at], data[at + 1]
        payload = data[at + 2: at + 2 + length]
        if len(payload) != length:
            raise ProtocolError(
                f"category 0x{category:02x} says {length} bytes, {len(payload)} followed"
            )
        out.append(Capability(category=category, payload=payload))
        at += 2 + length
    if at != len(data):
        raise ProtocolError("a capability list ended mid-entry")
    return tuple(out)


def set_configuration(
    acp_seid: int, int_seid: int, capabilities: tuple[Capability, ...], label: int = 0
) -> bytes:
    """Offer a configuration to one endpoint. This is the message that changes the sink's state."""
    for seid in (acp_seid, int_seid):
        if not 0 <= seid <= 0x3F:
            raise ProtocolError("a SEID is six bits")
    body = b"".join(capability.to_bytes() for capability in capabilities)
    return header(label, SIG_SET_CONFIGURATION) + bytes((acp_seid << 2, int_seid << 2)) + body


@dataclass(frozen=True)
class Reject:
    """What a refusal says: which category failed, and why."""

    category: int
    error: int

    def __str__(self) -> str:
        return f"category 0x{self.category:02x}, {error_name(self.error)}"


class SimulatedSink:
    """An in-process sink, so the probe's own code can be exercised without hardware.

    What it answers is split deliberately. Discover and the capability reads answer from
    `a2dp.ENDPOINTS`, which is the firmware's own table, so those replies are as proven as the
    rest of this project. **Its SetConfiguration policy is its own invention**: it accepts the
    advertised bytes exactly and refuses anything else with INVALID_CODEC_PARAMETER. That is a
    choice made to exercise both branches of the probe, not a claim about what the earbuds do,
    and it is the question the probe exists to ask. Reading a simulated refusal as evidence
    about the device would be reading this docstring backwards.
    """

    def __init__(self, endpoints: dict[int, tuple[str, bytes]]) -> None:
        # One SEID per codec, numbered from 1 in the order the firmware registers them.
        self.by_seid: dict[int, tuple[int, bytes]] = {
            seid: (codec, endpoints[codec][1])
            for seid, codec in enumerate(sorted(endpoints), start=1)
        }

    def answer(self, message: bytes) -> bytes:
        head = parse_header(message)
        if head.signal == SIG_DISCOVER:
            body = b"".join(
                bytes(((seid << 2), 0x08)) for seid in sorted(self.by_seid)
            )
            return header(head.label, SIG_DISCOVER, RESPONSE_ACCEPT) + body
        if head.signal in (SIG_GET_CAPABILITIES, SIG_GET_ALL_CAPABILITIES):
            seid = message[2] >> 2
            if seid not in self.by_seid:
                return header(head.label, head.signal, RESPONSE_REJECT) + bytes((0x12,))
            codec, specific = self.by_seid[seid]
            body = MEDIA_TRANSPORT.to_bytes() + media_codec(codec, specific).to_bytes()
            return header(head.label, head.signal, RESPONSE_ACCEPT) + body
        if head.signal == SIG_SET_CONFIGURATION:
            seid = message[2] >> 2
            if seid not in self.by_seid:
                return header(head.label, head.signal, RESPONSE_REJECT) + bytes(
                    (CATEGORY_MEDIA_CODEC, 0x12)
                )
            codec, specific = self.by_seid[seid]
            offered = parse_capabilities(message[4:])
            for capability in offered:
                if capability.category != CATEGORY_MEDIA_CODEC:
                    continue
                if capability.codec != codec or capability.codec_specific != specific:
                    return header(head.label, head.signal, RESPONSE_REJECT) + bytes(
                        (CATEGORY_MEDIA_CODEC, 0xE0)
                    )
            return header(head.label, head.signal, RESPONSE_ACCEPT)
        return header(head.label, head.signal, GENERAL_REJECT)


def parse_set_configuration_response(data: bytes) -> Reject | None:
    """None when the configuration was accepted, otherwise what the sink objected to.

    A SetConfiguration reject carries the failing service category and then the error code,
    which is what makes this probe able to say *which* field a device validates rather than
    only that something was wrong.
    """
    head = parse_header(data)
    if head.signal != SIG_SET_CONFIGURATION:
        raise ProtocolError("not a SetConfiguration response")
    if head.message_type == RESPONSE_ACCEPT:
        return None
    if head.message_type == GENERAL_REJECT:
        return Reject(category=0, error=0)
    if len(data) < 4:
        raise ProtocolError("a SetConfiguration reject carries a category and an error")
    return Reject(category=data[2], error=data[3])
