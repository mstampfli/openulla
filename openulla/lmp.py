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
  escape 0x7B / 0x1C     band ranges: (band, first channel, length) triples and no count
                         field, at most five  [fw acib 0x29964 and the builder 0x3abda
                         sending, 0x3bfc2 and 0x29b38 receiving]

Each end of 0x1C keeps the number of triples in its own field block and reads it off the
length: the sender copies three bytes per triple and reports 3n + 2, and the receiver divides
(PDU length - 2) by 3 [fw acib 0x3abda, 0x3bfc2]. The length is the count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from . import octets, packets
from .errors import ProtocolError, Refused

OPCODE_ACCEPTED = 3
OPCODE_NOT_ACCEPTED = 4
OPCODE_ULLA_SETUP = 0x73
OPCODE_ULLA_CONFIG = 0x74
OPCODE_ULLA_CONFIG_ALT = 0x75  # the same seven bytes, a different receiver
OPCODE_ULLA_BARE = 0x76  # the header alone
ESCAPE_FIRST = 0x7B  # the opcode from which an extended opcode byte follows
ESCAPE_7B = 0x7B
ESCAPE_7F = 0x7F
EXT_FEATURE_REQUEST = 0x0A  # in the 0x7B space: one word, the sender's feature bits
EXT_FEATURE_REPLY = 0x0B
EXT_SELECTOR_REQUEST = 0x0E  # 0x7B: one selector byte, answered with the pair below (5.11)
EXT_VALUE_AND_CODE = 0x0F  # 0x7B: a value byte and a four-bit code; 0x7F/0x20 is the same
EXT_PACKET_TABLE = 0x0D  # 0x7B: Apple's packet-type-table request, the counterpart of 0x7F/0x0B
EXT_LINK_HALFWORD_REQUEST = 0x10  # 0x7B: one halfword each way, kept at the link's +0x62
EXT_LINK_HALFWORD_REPLY = 0x11
EXT_BAND_BLOCK_REQUEST = 0x15  # 0x7B: a counted byte block, in the band module (5.15)
EXT_BAND_BLOCK_REPLY = 0x16
# The procedure number a link must be running for a message to be accepted, at its +0x7D.
# The band module's four messages all want 0x2F, which includes the two of section 6, and the
# packet-type-table request wants 0x20 [fw acib 0x297de, 0x29a7c, 0x29ad2, 0x29b48, 0x29e0e;
# docs/protocol.md 5.15]. An implementation that sends a band PDU outside 0x2F is refused
# whatever the feature bits say.
PROCEDURE_BAND_MODULE = 0x2F
PROCEDURE_PACKET_TABLE = 0x20
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

    Where each field comes from is proven through the transition context [docs/protocol.md
    5.1]: radio_value is a hardware read with retries, so it is not predictable from firmware
    [fw acib 0xdc9c0]; config_word is parameters 3 and 4 of the vendor command; `flags`, the
    byte at offset 7, is bits 0 and 1 of its parameter 5, so it is a value 0 to 3; and
    `extra_flags`, the byte at offset 8, is bit 3 of that parameter as a whole byte of 0 or 1.
    The command's parameter 2 becomes a byte the sender writes past the declared length, so it
    never reaches the air and is not modelled here. None of the fields is read by the accept
    decision [fw acib 0x675f4].
    """

    radio_value: int
    config_word: int
    flags: int
    extra_flags: int
    tid: int = 0

    LENGTH = 9

    def to_bytes(self) -> bytes:
        return (
            bytes((header(OPCODE_ULLA_SETUP, self.tid),))
            + octets.p32(self.radio_value, "radio_value")
            + octets.p16(self.config_word, "config_word")
            + octets.p8(self.flags, "flags")
            + octets.p8(self.extra_flags, "extra_flags")
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> SetupRequest:
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
    """Opcodes 0x74 and 0x75, two values read by id from the controller's parameter table.

    Both values are computed, not zero: the ids go through property 0x20D into a second
    dispatcher of 54 entries, where the word's id reaches a 26-bit quantity built from a
    per-link byte and the halfword's reaches a module's own measurement less 0x57
    [fw acib 0xde8f2 the table, 0xc1f10 and 0xc1f6c; docs/protocol.md 5.2]. 0x74's receiver
    stores them without checking
    [fw acib 0x6352c]; 0x75 has the same seven bytes, because the sender's case serves both
    opcodes [fw acib 0x3a832], but its own receiver drops a halfword above 7499 and requires
    an active session on the link whose role byte is zero [fw acib 0x487a0, 0x2f550, 0x2f566,
    0x2f540]. What either message is for is open.
    """

    value32: int
    value16: int
    tid: int = 0
    opcode: int = OPCODE_ULLA_CONFIG

    LENGTH = 7
    MAX_VALUE16_FOR_75 = 0x1D4B  # the bound 0x75's receiver applies [fw acib 0x487ba]

    def to_bytes(self) -> bytes:
        if self.opcode not in (OPCODE_ULLA_CONFIG, OPCODE_ULLA_CONFIG_ALT):
            raise ProtocolError(f"0x{self.opcode:02x} is not one of the configuration opcodes")
        return (
            bytes((header(self.opcode, self.tid),))
            + octets.p32(self.value32, "value32")
            + octets.p16(self.value16, "value16")
        )

    def would_be_dropped(self) -> bool:
        """Whether 0x75's own bound refuses this one. [fw acib 0x487ba]"""
        return self.opcode == OPCODE_ULLA_CONFIG_ALT and self.value16 > self.MAX_VALUE16_FOR_75

    @classmethod
    def from_bytes(cls, data: bytes) -> Configuration:
        head = parse_header(data)
        if head.opcode not in (OPCODE_ULLA_CONFIG, OPCODE_ULLA_CONFIG_ALT):
            raise ProtocolError(f"opcode 0x{head.opcode:02x} is not the configuration")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the configuration is {cls.LENGTH} bytes, got {len(data)}")
        return cls(
            value32=octets.u32(data, 1),
            value16=octets.u16(data, 5),
            tid=head.tid,
            opcode=head.opcode,
        )


@dataclass(frozen=True)
class SessionBare:
    """Opcode 0x76: the header and nothing else. [fw acib 0x3aaaa sending, 0x3b9de receiving]

    Its receiver takes it only while the session is active on a link whose role byte is zero,
    the first two of the conditions 0x75 is held to [fw acib 0x2f550, 0x2f566]. What it means
    is open.
    """

    tid: int = 0

    LENGTH = 1

    def to_bytes(self) -> bytes:
        return bytes((header(OPCODE_ULLA_BARE, self.tid),))

    @classmethod
    def from_bytes(cls, data: bytes) -> SessionBare:
        head = parse_header(data)
        if head.opcode != OPCODE_ULLA_BARE:
            raise ProtocolError(f"opcode 0x{head.opcode:02x} is not the bare session message")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"that message is one byte, got {len(data)}")
        return cls(tid=head.tid)


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
    def from_bytes(cls, data: bytes) -> Accepted:
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
    def from_bytes(cls, data: bytes) -> NotAccepted:
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
    def from_bytes(cls, data: bytes) -> Features:
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
class LinkHalfword:
    """Escape 0x7B, extended opcodes 0x10 and 0x11: one halfword each way.

    A request and its answer. The request's consumer stores the halfword at the link's +0x62,
    sets the link's +0x3E and replies with extended opcode 0x11 as an immediate; the reply's
    consumer stores the same halfword in the same place and requires the link to be in
    procedure state 0x2F with its +0x80 at 1, which is the state the request's own sender
    writes [fw acib 0x2978c, 0x29798, 0x297a0, 0x297a2 for the request, 0x297c8, 0x297d6,
    0x297da, 0x297e2 for the reply; protocol.md 5.15 and 5.11].

    What the halfword means is open. The name is structural for that reason, and the one
    value this firmware is seen putting in the reply block is 0x5B [fw acib 0x297a6].
    """

    value: int
    reply: bool = False
    tid: int = 0

    LENGTH = 4  # header, extended opcode, one halfword
    SEEN_IN_REPLY = 0x5B

    @property
    def ext_opcode(self) -> int:
        return EXT_LINK_HALFWORD_REPLY if self.reply else EXT_LINK_HALFWORD_REQUEST

    def to_bytes(self) -> bytes:
        if not 0 <= self.value <= 0xFFFF:
            raise ProtocolError("the value is one halfword")
        return bytes((header(ESCAPE_7B, self.tid), self.ext_opcode)) + octets.p16(self.value)

    @classmethod
    def from_bytes(cls, data: bytes) -> LinkHalfword:
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode not in (
            EXT_LINK_HALFWORD_REQUEST,
            EXT_LINK_HALFWORD_REPLY,
        ):
            raise ProtocolError("not the halfword pair")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"its body is one halfword, got {len(data) - 2}")
        return cls(
            value=octets.u16(data, 2),
            reply=head.ext_opcode == EXT_LINK_HALFWORD_REPLY,
            tid=head.tid,
        )


@dataclass(frozen=True)
class BandBlock:
    """Escape 0x7B, extended opcodes 0x15 and 0x16: a counted byte block, each way.

    A request and its answer in the same module as both band messages of section 6: 0x15's
    consumer replies with extended opcode 0x16 as an immediate, and 0x16's consumer requires
    procedure state 0x2F and then tests **bit 5** of the AND of the two ends' capability
    bytes, which is the band family bit [fw acib 0x29a1c, 0x29a2a, 0x29a2c for the request,
    0x29a70 to 0x29a96 for the reply; protocol.md 5.15].

    The body is a count byte followed by that many bytes [5.7's builder for 0x15, 0x16 and
    0x24], and those bytes are a **TLV list**: each item's first byte carries a four-bit type in
    its low nibble and a four-bit length in its high nibble, and a reader advances by one plus
    that length, so an unknown type is skipped rather than fatal [fw acib 0x29810 the parser,
    0x2983a the type, 0x298ba to 0x298c4 the skip; protocol.md 5.15].
    """

    block: bytes = b""
    reply: bool = False
    tid: int = 0

    FEATURE_BIT = 5  # the band family bit its reply is gated on, the same as FEATURE_BIT_BANDS
    # The four types this firmware handles, and the per-link field each one writes.
    ITEM_FIELDS: ClassVar[dict[int, int]] = {0: 0x62, 1: 0x64, 2: 0x68, 0xF: 0x66}
    # What this firmware's own reply opens with: count 0x0B, then type 0 of length 2 carrying
    # 0x005B, which is the value the halfword pair carries to the same field [fw acib 0x29a68].
    REPLY_OPENS_WITH = bytes.fromhex("205b00")

    @property
    def ext_opcode(self) -> int:
        return EXT_BAND_BLOCK_REPLY if self.reply else EXT_BAND_BLOCK_REQUEST

    def to_bytes(self) -> bytes:
        if len(self.block) > 0xFF:
            raise ProtocolError("the count is one byte, so at most 255 follow it")
        return (
            bytes((header(ESCAPE_7B, self.tid), self.ext_opcode, len(self.block)))
            + self.block
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> BandBlock:
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode not in (
            EXT_BAND_BLOCK_REQUEST,
            EXT_BAND_BLOCK_REPLY,
        ):
            raise ProtocolError("not the counted band block")
        if len(data) < 3:
            raise ProtocolError("the count byte is missing")
        count = data[2]
        if len(data) != 3 + count:
            raise ProtocolError(f"its count says {count} bytes, {len(data) - 3} followed")
        return cls(
            block=data[3:],
            reply=head.ext_opcode == EXT_BAND_BLOCK_REPLY,
            tid=head.tid,
        )

    def items(self) -> tuple[tuple[int, bytes], ...]:
        """Walk the block as TLV, the way the firmware's parser does.

        A trailing item whose declared length runs past the end is returned with the bytes that
        are actually there, because that is what the firmware would read; it bounds its walk by
        the count and not by each item.
        """
        out: list[tuple[int, bytes]] = []
        at = 0
        while at < len(self.block):
            head_byte = self.block[at]
            kind, length = head_byte & 0x0F, head_byte >> 4
            out.append((kind, self.block[at + 1: at + 1 + length]))
            at += 1 + length
        return tuple(out)

    @classmethod
    def from_items(cls, items: list[tuple[int, bytes]], reply: bool = False,
                   tid: int = 0) -> BandBlock:
        """Build a block from (type, value) pairs, each one its own length."""
        block = bytearray()
        for kind, value in items:
            if not 0 <= kind <= 0x0F:
                raise ProtocolError("the item type is four bits")
            if len(value) > 0x0F:
                raise ProtocolError("the item length is four bits, so at most 15 bytes")
            block.append((len(value) << 4) | kind)
            block += value
        return cls(block=bytes(block), reply=reply, tid=tid)


@dataclass(frozen=True)
class PacketTableRequest:
    """Escape 0x7B, extended opcode 0x0D: which packet-type table the link should use.

    The counterpart of the specification's 0x7F/0x0B request, and the same procedure in the
    firmware: one function sends whichever of the two the link's capability allows, both arms
    bound the single byte and refuse above it with 0x1E, both call the same collision check,
    and both join at the same address [fw acib 0x29e00 the sender, 0x3c0be this arm, 0x3c0c2
    its bound, 0x3be50 and 0x3be70 the specification's, 0x3bf80 the shared error]. The one
    difference is the bound: this form admits table 2, where the specification's admits 1
    [docs/protocol.md 5.13].

    Table 2 is what makes the six packet types of `openulla/packets.py` selectable, and those
    are the only non-standard ACL packet types this controller has.
    """

    table: int
    tid: int = 0

    LENGTH = 3  # header, extended opcode, the table

    def to_bytes(self) -> bytes:
        if not 0 <= self.table <= packets.MAX_TABLE:
            raise ProtocolError(
                f"the packet-type table is 0 to {packets.MAX_TABLE}; the receiver answers "
                "0x1e above that"
            )
        return bytes((header(ESCAPE_7B, self.tid), EXT_PACKET_TABLE, self.table))

    @classmethod
    def from_bytes(cls, data: bytes) -> PacketTableRequest:
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode != EXT_PACKET_TABLE:
            raise ProtocolError("not the packet-type-table request")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"its body is one byte, got {len(data) - 1}")
        if data[2] > packets.MAX_TABLE:
            raise Refused(0x1E, f"packet-type table {data[2]} is above the bound")
        return cls(table=data[2], tid=head.tid)

    def selectable(self) -> tuple[packets.PacketType, ...]:
        """Which packet types this table makes available."""
        return packets.for_table(self.table)


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
    def from_bytes(cls, data: bytes) -> AllowedBands:
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode != EXT_ALLOWED_BANDS:
            raise ProtocolError("not the allowed-bands PDU")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the allowed-bands body is 2 bytes, got {len(data) - 1}")
        return cls(mask=data[2], tid=head.tid)


@dataclass(frozen=True)
class SelectorRequest:
    """Escape 0x7B, extended opcode 0x0E: one selector byte, answered with the pair below.

    The receiver switches on the selector against 0, 1 and 2, asks the controller property
    0xB9 about the link with the parameter 0, 1 or 4, and answers [fw acib 0x291b0 onwards,
    protocol.md 5.11]. The names here are ours: nothing in the firmware names this message.

    **What it is**: Apple's form of the specification's power control request. One function
    sends either this or 0x7F/0x1F depending on the link [fw acib 0x28fa8 and 0x2906a, where
    both escape opcodes are set on the way in], the two are the same length, and the selector's
    range of 0, 1 and 2 is that request's own [protocol.md 5.14]. The structural name is kept
    because the identification, however direct, is this project's reading and not a string in
    the firmware; the same choice is made for the packet types in `packets.py`.
    """

    selector: int
    tid: int = 0

    LENGTH = 3  # header, extended opcode, selector
    SELECTORS = (0, 1, 2)

    def to_bytes(self) -> bytes:
        if self.selector not in self.SELECTORS:
            raise ProtocolError("the receiver answers only the selectors 0, 1 and 2")
        return bytes((header(ESCAPE_7B, self.tid), EXT_SELECTOR_REQUEST, self.selector))

    @classmethod
    def from_bytes(cls, data: bytes) -> SelectorRequest:
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode != EXT_SELECTOR_REQUEST:
            raise ProtocolError("not the selector request")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the selector body is 1 byte, got {len(data) - 2}")
        return cls(selector=data[2], tid=head.tid)


@dataclass(frozen=True)
class ValueAndCode:
    """Escape 0x7B, extended opcode 0x0F: a value byte and a four-bit code.

    The three pairs this firmware answers with are the same two numbers at one, two and three
    times [fw acib 0x291c4, 0x291d2, 0x291de]. Its receiver bounds the code at 15 and sorts it
    with two masks [fw acib 0x29332 onwards]. The same message goes out as 0x7F/0x20 instead
    when the feature bits say so, which is a sender's decision and not part of the layout
    [fw acib 0x29204, protocol.md 5.11].

    **What it is**: Apple's form of the specification's power control response, which packs
    three two-bit fields into one byte, one per modulation. This form adds a second byte bounded
    to four bits, so **five** such fields. Read that way the three pairs are one code repeated
    five times, which is why they looked like a 1:2:3 family, and the receiver's two masks fall
    exactly on two-bit boundaries. `fields()` does that decomposition [protocol.md 5.14].
    """

    value: int
    code: int
    tid: int = 0

    LENGTH = 4  # header, extended opcode, value, code
    PAIRS = ((0x15, 0x05), (0x2A, 0x0A), (0x3F, 0x0F))
    FIRST_CLASS = frozenset({0, 3, 12, 15})  # the mask 0x9009
    SECOND_CLASS = frozenset({2, 8, 10})  # the mask 0x504

    def to_bytes(self) -> bytes:
        if not 0 <= self.value <= 0xFF:
            raise ProtocolError("the value is one byte")
        if not 0 <= self.code <= 0x0F:
            raise ProtocolError("the receiver bounds the code at 15")
        return bytes((header(ESCAPE_7B, self.tid), EXT_VALUE_AND_CODE, self.value, self.code))

    @classmethod
    def from_bytes(cls, data: bytes) -> ValueAndCode:
        head = parse_header(data)
        if head.opcode != ESCAPE_7B or head.ext_opcode != EXT_VALUE_AND_CODE:
            raise ProtocolError("not the value-and-code PDU")
        if len(data) != cls.LENGTH:
            raise ProtocolError(f"the value-and-code body is 2 bytes, got {len(data) - 2}")
        return cls(value=data[2], code=data[3], tid=head.tid)

    def code_class(self) -> int | None:
        """1 or 2 for the two classes the receiver's masks pick out, None for neither."""
        if self.code in self.FIRST_CLASS:
            return 1
        if self.code in self.SECOND_CLASS:
            return 2
        return None

    def fields(self) -> tuple[int, int, int, int, int]:
        """The five two-bit fields: three from the value byte, two from the four-bit code.

        The specification's power control response carries three such fields, one per
        modulation; this form carries five [protocol.md 5.14]. The value byte's top two bits
        are not part of them, and the three answers this firmware gives have all five equal.
        """
        return (
            self.value & 3,
            (self.value >> 2) & 3,
            (self.value >> 4) & 3,
            self.code & 3,
            (self.code >> 2) & 3,
        )

    def uniform(self) -> int | None:
        """The one code all five fields carry, or None when they differ."""
        seen = set(self.fields())
        return seen.pop() if len(seen) == 1 else None


@dataclass(frozen=True)
class BandRange:
    """One (band, first channel, length) triple, as both 0x1C and HCI 0xFF8E carry it."""

    band: int
    first_channel: int
    length: int

    def to_bytes(self) -> bytes:
        return (
            octets.p8(self.band, "band")
            + octets.p8(self.first_channel, "first_channel")
            + octets.p8(self.length, "length")
        )


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
    def from_bytes(cls, data: bytes) -> BandRanges:
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
ESCAPE_7B_REFUSED = frozenset(
    {0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x12, 0x13, 0x1D, 0x1E, 0x24}
)
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
    if head.opcode in (OPCODE_ULLA_CONFIG, OPCODE_ULLA_CONFIG_ALT):
        return Configuration.from_bytes(data)
    if head.opcode == OPCODE_ULLA_BARE:
        return SessionBare.from_bytes(data)
    if head.opcode == OPCODE_ACCEPTED:
        return Accepted.from_bytes(data)
    if head.opcode == OPCODE_NOT_ACCEPTED:
        return NotAccepted.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode in (
        EXT_FEATURE_REQUEST,
        EXT_FEATURE_REPLY,
    ):
        return Features.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_PACKET_TABLE:
        return PacketTableRequest.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode in (
        EXT_LINK_HALFWORD_REQUEST,
        EXT_LINK_HALFWORD_REPLY,
    ):
        return LinkHalfword.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode in (
        EXT_BAND_BLOCK_REQUEST,
        EXT_BAND_BLOCK_REPLY,
    ):
        return BandBlock.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_ALLOWED_BANDS:
        return AllowedBands.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_BAND_RANGES:
        return BandRanges.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_SELECTOR_REQUEST:
        return SelectorRequest.from_bytes(data)
    if head.opcode == ESCAPE_7B and head.ext_opcode == EXT_VALUE_AND_CODE:
        return ValueAndCode.from_bytes(data)
    raise ProtocolError(
        f"opcode 0x{head.opcode:02x}"
        + (f" extended 0x{head.ext_opcode:02x}" if head.ext_opcode is not None else "")
        + " is not one of the PDUs this module carries"
    )
