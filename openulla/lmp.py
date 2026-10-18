"""The link-manager PDUs that carry the ULLA setup between two controllers.

These never cross the host interface, so a host capture cannot show them; the layouts come
from the firmware's own senders and parsers, which agree field for field.

  header byte            opcode in bits 1-7, transaction id in bit 0  [fw acib 0x3a634]
  extended opcode        the next byte, for opcodes 0x7B and above
                         [fw acib 0x3af66, 0x3af9c..0x3afb4]
  0x73 setup request     9 bytes; sender acib 0x3aa6e, reader acib 0x675f4
  0x74 configuration     7 bytes; sender acib 0x3a832, reader acib 0x6352c
  accepted               opcode 3, one body byte, the accepted opcode
                         [fw acib 0x3d19c, which passes 3 to the send builder]
  not accepted           opcode 4, two body bytes, the rejected opcode and a reason
                         [fw acib 0x3c934, which passes 4 and stores the pair]
  escape 0x7B / 0x17     allowed-band mask, one payload byte, body length 2
                         [fw acib 0x2992a sending, 0x3b558 checking the length]
  escape 0x7B / 0x1C     band ranges: a count, then (band, first channel, length) triples,
                         at most five  [fw acib 0x29964 sending, 0x3bfc2 and 0x29b38 receiving]

The receiver of 0x1C recomputes the number of triples as (body length - 2) / 3 instead of
trusting the count byte [fw acib 0x3bfc2], so a sender that disagrees with itself is read by
its length.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ProtocolError

OPCODE_ACCEPTED = 3
OPCODE_NOT_ACCEPTED = 4
OPCODE_ULLA_SETUP = 0x73
OPCODE_ULLA_CONFIG = 0x74
ESCAPE_FIRST = 0x7B  # the opcode from which an extended opcode byte follows
ESCAPE_7B = 0x7B
ESCAPE_7F = 0x7F
EXT_FEATURE_REQUEST = 0x0A  # in the 0x7B space: one word, the sender's feature bits
EXT_FEATURE_REPLY = 0x0B
EXT_ALLOWED_BANDS = 0x17  # in the 0x7B space; 0x17 also exists behind 0x7F, unrelated
EXT_BAND_RANGES = 0x1C
MAX_BAND_RANGES = 5
# Bits of the feature word, by what they gate. Both are tested against the AND of the two
# ends' words, so a peer that never answers the exchange gates everything off.
FEATURE_BIT_BANDS = 5  # the band command family and the band PDUs [fw acib 0x29916]
FEATURE_BIT_MODE_PROCEDURE = 6  # 0xFE50's non-zero path [fw acib 0x1db5e]
FEATURE_REDUCED_LOW_BYTE = 0xA9  # what a quirked link advertises [fw acib 0x2942c, 0x294a8]


def header(opcode: int, tid: int) -> int:
    """The first byte of every PDU. [fw acib 0x3a634]"""
    if not 0 <= opcode <= 0x7F:
        raise ProtocolError("an LMP opcode is seven bits")
    if tid not in (0, 1):
        raise ProtocolError("a transaction id is one bit")
    return (opcode << 1) | tid


@dataclass(frozen=True)
class Head:
    """A parsed PDU header."""

    opcode: int
    tid: int
    ext_opcode: int | None
    body_offset: int


def parse_header(data: bytes) -> Head:
    """Split the header the way the firmware's parser does. [fw acib 0x3af66, 0x3af9c]"""
    if not data:
        raise ProtocolError("empty PDU")
    opcode, tid = data[0] >> 1, data[0] & 1
    if opcode < ESCAPE_FIRST:
        return Head(opcode, tid, None, 1)
    if len(data) < 2:
        raise ProtocolError(f"opcode 0x{opcode:02x} needs an extended opcode byte")
    return Head(opcode, tid, data[1], 2)


@dataclass(frozen=True)
class SetupRequest:
    """Opcode 0x73. The fields the source fills in and the sink copies. [fw acib 0x3aa6e]

    radio_value is read from the source's own radio hardware at runtime, with a validity
    check and up to ten retries [fw acib 0xdc9c0], so it is not predictable from firmware.
    config_word and the flag bytes come from the host's own vendor command (hci.UllaSetup).
    None of them is read by the accept decision [fw acib 0x675f4].
    """

    radio_value: int
    config_word: int
    flags: int
    extra_flags: int
    tid: int = 0

    LENGTH = 9

    def to_bytes(self) -> bytes:
        if not 0 <= self.radio_value <= 0xFFFFFFFF:
            raise ProtocolError("radio_value is four bytes")
        if not 0 <= self.config_word <= 0xFFFF:
            raise ProtocolError("config_word is two bytes")
        for name in ("flags", "extra_flags"):
            if not 0 <= getattr(self, name) <= 0xFF:
                raise ProtocolError(f"{name} is one byte")
        return (
            bytes((header(OPCODE_ULLA_SETUP, self.tid),))
            + self.radio_value.to_bytes(4, "little")
            + self.config_word.to_bytes(2, "little")
            + bytes((self.flags, self.extra_flags))
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "SetupRequest":
        head = parse_header(data)
        if head.opcode != OPCODE_ULLA_SETUP:
            raise ProtocolError(f"opcode 0x{head.opcode:02x} is not the setup request")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the setup request is {cls.LENGTH} bytes, got {len(data)}")
        return cls(
            radio_value=int.from_bytes(data[1:5], "little"),
            config_word=int.from_bytes(data[5:7], "little"),
            flags=data[7],
            extra_flags=data[8],
            tid=head.tid,
        )


@dataclass(frozen=True)
class Configuration:
    """Opcode 0x74, two values read by id from the controller's parameter table.

    In this build both getters are stubs that return nothing [fw acib 0xcf9ac, 0xcf9ae], so
    this firmware sends zeroes; the receiver stores them without checking [fw acib 0x6352c].
    """

    value32: int
    value16: int
    tid: int = 0

    LENGTH = 7

    def to_bytes(self) -> bytes:
        if not 0 <= self.value32 <= 0xFFFFFFFF or not 0 <= self.value16 <= 0xFFFF:
            raise ProtocolError("configuration values are four and two bytes")
        return (
            bytes((header(OPCODE_ULLA_CONFIG, self.tid),))
            + self.value32.to_bytes(4, "little")
            + self.value16.to_bytes(2, "little")
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Configuration":
        head = parse_header(data)
        if head.opcode != OPCODE_ULLA_CONFIG:
            raise ProtocolError(f"opcode 0x{head.opcode:02x} is not the configuration")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the configuration is {cls.LENGTH} bytes, got {len(data)}")
        return cls(
            value32=int.from_bytes(data[1:5], "little"),
            value16=int.from_bytes(data[5:7], "little"),
            tid=head.tid,
        )


@dataclass(frozen=True)
class Accepted:
    """The standard accepted PDU, carrying the opcode it accepts. [fw acib 0x3d19c]"""

    accepted_opcode: int
    tid: int = 0

    LENGTH = 2

    def to_bytes(self) -> bytes:
        if not 0 <= self.accepted_opcode <= 0xFF:
            raise ProtocolError("the accepted opcode is one byte")
        return bytes((header(OPCODE_ACCEPTED, self.tid), self.accepted_opcode))

    @classmethod
    def from_bytes(cls, data: bytes) -> "Accepted":
        head = parse_header(data)
        if head.opcode != OPCODE_ACCEPTED:
            raise ProtocolError(f"opcode 0x{head.opcode:02x} is not accepted")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"accepted is {cls.LENGTH} bytes, got {len(data)}")
        return cls(accepted_opcode=data[1], tid=head.tid)


@dataclass(frozen=True)
class NotAccepted:
    """The standard not-accepted PDU: the rejected opcode and a reason. [fw acib 0x3c934]"""

    rejected_opcode: int
    reason: int
    tid: int = 0

    LENGTH = 3

    def to_bytes(self) -> bytes:
        if not 0 <= self.rejected_opcode <= 0xFF or not 0 <= self.reason <= 0xFF:
            raise ProtocolError("the rejected opcode and the reason are one byte each")
        return bytes((header(OPCODE_NOT_ACCEPTED, self.tid), self.rejected_opcode, self.reason))

    @classmethod
    def from_bytes(cls, data: bytes) -> "NotAccepted":
        head = parse_header(data)
        if head.opcode != OPCODE_NOT_ACCEPTED:
            raise ProtocolError(f"opcode 0x{head.opcode:02x} is not not-accepted")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"not-accepted is {cls.LENGTH} bytes, got {len(data)}")
        return cls(rejected_opcode=data[1], reason=data[2], tid=head.tid)


@dataclass(frozen=True)
class Features:
    """Escape 0x7B, extended opcodes 0x0A and 0x0B: one four-byte feature word each way.

    The request carries the sender's own word; the receiver stores it against the link and
    answers with its own [fw acib 0x3c0a8 storing the body word, then 0x29470 which keeps it
    at the link's +0x47 and sends the reply; the builder for both is acib 0x3abc8].

    Everything else is gated on the AND of the two words, so an implementation that does not
    answer this is never sent a band PDU and never gets the mode procedure. See `agreed`.
    """

    word: int
    reply: bool = False
    tid: int = 0

    LENGTH = 6  # header, extended opcode, four bytes

    @property
    def ext_opcode(self) -> int:
        return EXT_FEATURE_REPLY if self.reply else EXT_FEATURE_REQUEST

    def to_bytes(self) -> bytes:
        if not 0 <= self.word <= 0xFFFFFFFF:
            raise ProtocolError("the feature word is four bytes")
        return (
            bytes((header(ESCAPE_7B, self.tid), self.ext_opcode))
            + self.word.to_bytes(4, "little")
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Features":
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode not in (
            EXT_FEATURE_REQUEST,
            EXT_FEATURE_REPLY,
        ):
            raise ProtocolError("not the feature exchange")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the feature exchange is {cls.LENGTH} bytes, got {len(data)}")
        return cls(
            word=int.from_bytes(data[2:6], "little"),
            reply=head.ext_opcode == EXT_FEATURE_REPLY,
            tid=head.tid,
        )


def agreed(own: int, peer: int, bit: int, byte: int = 0) -> bool:
    """Whether one feature bit is usable on a link: both words must carry it.

    The firmware ANDs the two words a byte at a time and tests one bit of the result
    [fw acib 0x29916 for bit 5 of byte 0, 0x1db5e for bit 6 of byte 0 or of byte 3].
    """
    if not 0 <= bit <= 7 or not 0 <= byte <= 3:
        raise ProtocolError("a feature bit is one of eight, in one of four bytes")
    own_byte = (own >> (8 * byte)) & 0xFF
    peer_byte = (peer >> (8 * byte)) & 0xFF
    return bool(own_byte & peer_byte & (1 << bit))


def mode_procedure_agreed(own: int, peer: int) -> bool:
    """The gate on 0xFE50's non-zero path: bit 6 of byte 0, or of byte 3. [fw acib 0x1db5e]"""
    return agreed(own, peer, FEATURE_BIT_MODE_PROCEDURE) or agreed(
        own, peer, FEATURE_BIT_MODE_PROCEDURE, byte=3
    )


@dataclass(frozen=True)
class AllowedBands:
    """Escape 0x7B, extended opcode 0x17: the sender's own allowed-band mask.

    Body length 2, checked on receipt [fw acib 0x3b558]; the payload byte is read as the mask
    [fw acib 0x3d55a, then 0x29ab4].
    """

    mask: int
    tid: int = 0

    LENGTH = 3  # header, extended opcode, mask

    def to_bytes(self) -> bytes:
        if not 0 <= self.mask <= 0xFF:
            raise ProtocolError("the allowed-band mask is one byte")
        return bytes((header(ESCAPE_7B, self.tid), EXT_ALLOWED_BANDS, self.mask))

    @classmethod
    def from_bytes(cls, data: bytes) -> "AllowedBands":
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode != EXT_ALLOWED_BANDS:
            raise ProtocolError("not the allowed-bands PDU")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the allowed-bands body is 2 bytes, got {len(data) - 1}")
        return cls(mask=data[2], tid=head.tid)


@dataclass(frozen=True)
class BandRange:
    """One (band, first channel, length) triple, as both 0x1C and HCI 0xFF8E carry it."""

    band: int
    first_channel: int
    length: int

    def to_bytes(self) -> bytes:
        for name in ("band", "first_channel", "length"):
            if not 0 <= getattr(self, name) <= 0xFF:
                raise ProtocolError(f"{name} is one byte")
        return bytes((self.band, self.first_channel, self.length))


@dataclass(frozen=True)
class BandRanges:
    """Escape 0x7B, extended opcode 0x1C: which channels are used within each band.

    The triples follow the extended opcode with **no count byte in front of them**: the
    sender keeps its count in its own field block and copies three times that many bytes onto
    the wire [fw acib 0x3abda], and the receiver recovers the count by dividing, as
    (PDU length - 2) / 3, before storing it in the same place in its own block
    [fw acib 0x3bfc2]. So the length is the count, both ways round.

    The sender packs at most five triples and skips band 3 and any band whose range is zero
    [fw acib 0x29964]; the receiver refuses more than five [fw acib 0x29b38] and stores each
    pair in an eight-entry per-link array at the link's offset 0x49F
    [fw acib 0x29b8c..0x29b9a]. The division truncates, so a PDU whose length is not 2 plus a
    multiple of three has its trailing bytes ignored rather than refused, which this mirrors.
    """

    ranges: tuple[BandRange, ...] = ()
    tid: int = 0
    ignored: bytes = field(default=b"", compare=False)

    def to_bytes(self) -> bytes:
        if len(self.ranges) > MAX_BAND_RANGES:
            raise ProtocolError(f"at most {MAX_BAND_RANGES} triples fit")
        body = b"".join(r.to_bytes() for r in self.ranges)
        return bytes((header(ESCAPE_7B, self.tid), EXT_BAND_RANGES)) + body

    @classmethod
    def from_bytes(cls, data: bytes) -> "BandRanges":
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode != EXT_BAND_RANGES:
            raise ProtocolError("not the band-ranges PDU")
        count = (len(data) - 2) // 3  # the firmware's own formula, on the whole PDU
        if count > MAX_BAND_RANGES:
            raise ProtocolError(f"more than {MAX_BAND_RANGES} triples")
        triples = tuple(
            BandRange(data[2 + 3 * i], data[3 + 3 * i], data[4 + 3 * i]) for i in range(count)
        )
        return cls(ranges=triples, tid=head.tid, ignored=data[2 + 3 * count :])


# Which extended opcodes each escape space implements in build 9A348, and which share the
# reject stub with the out-of-range case. Read from the two dispatch tables
# [fw acib 0x3bc14 for 0x7B, 39 entries; 0x3b3bc for 0x7F, 34 entries]. An implementation can
# use these to know what an earbud of this build will answer at all; see protocol.md 5.6.
ESCAPE_7B_EXT = range(1, 0x28)
ESCAPE_7B_REFUSED = frozenset({0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x12, 0x13, 0x1D, 0x1E, 0x24})
ESCAPE_7B_IMPLEMENTED = frozenset(ESCAPE_7B_EXT) - ESCAPE_7B_REFUSED
ESCAPE_7F_EXT = range(1, 0x23)
ESCAPE_7F_REFUSED = frozenset({0x08, 0x09, 0x0A, 0x0E, 0x0F, 0x12, 0x13, 0x14})
ESCAPE_7F_IMPLEMENTED = frozenset(ESCAPE_7F_EXT) - ESCAPE_7F_REFUSED


def implemented(escape: int, ext_opcode: int) -> bool:
    """Would an earbud of this build dispatch this extended PDU, rather than refuse it?"""
    if escape == ESCAPE_7B:
        return ext_opcode in ESCAPE_7B_IMPLEMENTED
    if escape == ESCAPE_7F:
        return ext_opcode in ESCAPE_7F_IMPLEMENTED
    raise ProtocolError(f"0x{escape:02x} is not an escape opcode with a dispatch table")


def parse(data: bytes) -> object:
    """Parse any of the PDUs this module knows, by opcode."""
    head = parse_header(data)
    if head.opcode == OPCODE_ULLA_SETUP:
        return SetupRequest.from_bytes(data)
    if head.opcode == OPCODE_ULLA_CONFIG:
        return Configuration.from_bytes(data)
    if head.opcode == OPCODE_ACCEPTED:
        return Accepted.from_bytes(data)
    if head.opcode == OPCODE_NOT_ACCEPTED:
        return NotAccepted.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode in (
        EXT_FEATURE_REQUEST,
        EXT_FEATURE_REPLY,
    ):
        return Features.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_ALLOWED_BANDS:
        return AllowedBands.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_BAND_RANGES:
        return BandRanges.from_bytes(data)
    raise ProtocolError(
        f"opcode 0x{head.opcode:02x}"
        + (f" extended 0x{head.ext_opcode:02x}" if head.ext_opcode is not None else "")
        + " is not one of the PDUs this module carries"
    )
