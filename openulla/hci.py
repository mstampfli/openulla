"""The HCI vendor commands and the vendor event that drive the controller.

An HCI opcode is the group shifted left by ten, or'ed with the command; the vendor group is
0x3F, so every opcode here reads 0xFC00 | command, and the command ids the earbud's own code
uses (0x250, 0x379, ...) are the low ten bits of these opcodes.

  0xFE50 start the transition  7 parameter bytes  [fw acib table 0xf1928, handler 0x1da40]
  0xFF77 band gate            1 byte             [fw acib 0x192dc]
  0xFF78 read a link's band   2 bytes, a handle  [fw acib 0x19324, 0x193a0]
  0xFF79 allowed-band mask    1 byte             [fw acib 0x193f4, host side rkof 0x24b04]
  0xFF7A the same mask again  1 byte             [fw acib 0x19528, host side rkof 0x249e0]
  0xFF7C set a link's band    3 bytes            [fw acib 0x19704, 0x19832, 0x197f4]
  0xFF8E per-band ranges      count, then 3 each [fw acib 0x19f9c]
  0xFF92 country code / band  count, then 1 each [fw acib 0x1e074, host side rkof 0x248f8]
  0xFFB3 test band and range  3 bytes            [fw acib 0x18d04, 0x18d10]
  0xFD31 one country code     1 byte             [fw acib 0x1e0de]
  event 0xFF, 7 parameters    00 0A, handle, mode, result, reason
                              [fw acib 0x11d34, the four stores at 0x11d4a..0x11d52; the
                              allocator at 0x129ea moves the result into r8 before the store]
  event 0xFF, 5 parameters    09 0A, handle, one byte  [fw acib 0x11d74, caller 0x10590]

The refusals are the firmware's own: every band command is gated on bit 5 of the feature byte
that 0xFF77 sets [fw acib 0x19430, 0x10003290], a mask of 0 or with bit 3 set is refused, a
band index must be 0..7 (8 meaning none), a country code must be 0..7 or exactly 0xFF, and
0xFFB3 refuses index 3 outright.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import bands
from .codes import Mode, Result
from .errors import ProtocolError

OGF_VENDOR = 0x3F
H4_COMMAND = 0x01
H4_EVENT = 0x04
EVENT_VENDOR = 0xFF

OPCODE_ULLA_SETUP = 0xFE50
OPCODE_BAND_GATE = 0xFF77
OPCODE_READ_LINK_BAND = 0xFF78
OPCODE_ALLOWED_BANDS = 0xFF79
OPCODE_ALLOWED_BANDS_AGAIN = 0xFF7A
OPCODE_SET_LINK_BAND = 0xFF7C
OPCODE_BAND_RANGES = 0xFF8E
OPCODE_COUNTRY_CODES = 0xFF92
OPCODE_TEST_BAND_RANGE = 0xFFB3
OPCODE_COUNTRY_CODE = 0xFD31

EVENT_ID_MODE_CHANGE = 0x0A00
EVENT_ID_SECOND = 0x0A09


def opcode(ogf: int, ocf: int) -> int:
    if not 0 <= ogf <= 0x3F or not 0 <= ocf <= 0x3FF:
        raise ProtocolError("an HCI opcode is six bits of group and ten of command")
    return (ogf << 10) | ocf


def vendor_opcode(ocf: int) -> int:
    """The full opcode of a vendor command id, the way the earbud's own code names them."""
    return opcode(OGF_VENDOR, ocf)


def command(op: int, params: bytes = b"") -> bytes:
    """One HCI command packet, without the H4 transport byte."""
    if not 0 <= op <= 0xFFFF:
        raise ProtocolError("an opcode is two bytes")
    if len(params) > 0xFF:
        raise ProtocolError("command parameters are at most 255 bytes")
    return op.to_bytes(2, "little") + bytes((len(params),)) + params


def h4(packet: bytes, kind: int = H4_COMMAND) -> bytes:
    """Prefix a packet with its H4 transport byte, for a UART or a vhci write."""
    return bytes((kind,)) + packet


@dataclass(frozen=True)
class UllaSetup:
    """Parameters of 0xFE50, the command that starts the whole transition.

    `extra_flags` is copied into the last byte of the setup request PDU, `config_word` into
    its configuration word, and bits 0, 1 and 3 of `flags` into its flag byte; bit 2 is used
    elsewhere. `procedure` 0 starts the transition and other values select another procedure.
    [fw acib 0x1da40]
    """

    handle: int
    extra_flags: int
    config_word: int
    flags: int
    procedure: int = 0

    LENGTH = 7

    def to_bytes(self) -> bytes:
        if not 0 <= self.handle <= 0x0EFF:
            raise ProtocolError("a connection handle is twelve bits")
        for name in ("extra_flags", "flags", "procedure"):
            if not 0 <= getattr(self, name) <= 0xFF:
                raise ProtocolError(f"{name} is one byte")
        if not 0 <= self.config_word <= 0xFFFF:
            raise ProtocolError("config_word is two bytes")
        return (
            self.handle.to_bytes(2, "little")
            + bytes((self.extra_flags,))
            + self.config_word.to_bytes(2, "little")
            + bytes((self.flags, self.procedure))
        )

    @classmethod
    def from_bytes(cls, params: bytes) -> "UllaSetup":
        if len(params) != cls.LENGTH:
            raise ProtocolError(f"0xFE50 takes {cls.LENGTH} parameter bytes, got {len(params)}")
        return cls(
            handle=int.from_bytes(params[0:2], "little"),
            extra_flags=params[2],
            config_word=int.from_bytes(params[3:5], "little"),
            flags=params[5],
            procedure=params[6],
        )

    def command(self) -> bytes:
        return command(OPCODE_ULLA_SETUP, self.to_bytes())


def band_gate(enable: bool) -> bytes:
    """0xFF77: the switch that gates every other band command. [fw acib 0x192dc]"""
    return command(OPCODE_BAND_GATE, bytes((1 if enable else 0,)))


def read_link_band(handle: int) -> bytes:
    """0xFF78: read a link's current band as an index. [fw acib 0x19324]"""
    if not 0 <= handle <= 0x0EFF:
        raise ProtocolError("a connection handle is twelve bits")
    return command(OPCODE_READ_LINK_BAND, handle.to_bytes(2, "little"))


def allowed_bands(mask: int) -> tuple[bytes, bytes]:
    """0xFF79 and 0xFF7A, which always travel as a pair. [fw rkof 0x24b04, 0x249e0]"""
    bands.check_allowed_mask(mask)
    payload = bytes((mask,))
    return command(OPCODE_ALLOWED_BANDS, payload), command(OPCODE_ALLOWED_BANDS_AGAIN, payload)


def set_link_band(handle: int, index: int) -> bytes:
    """0xFF7C: set a link's band, 0..7, or 8 for none. [fw acib 0x19704]"""
    if not 0 <= handle <= 0x0EFF:
        raise ProtocolError("a connection handle is twelve bits")
    if not 0 <= index <= bands.NO_BAND:
        raise ProtocolError(f"a band index is 0..7, or {bands.NO_BAND} for none")
    return command(OPCODE_SET_LINK_BAND, handle.to_bytes(2, "little") + bytes((index,)))


def band_ranges(ranges: Sequence[tuple[int, int, int]]) -> bytes:
    """0xFF8E: per-band channel ranges, each checked against the band table. [fw acib 0x19f9c]"""
    if not 1 <= len(ranges) <= bands.BAND_COUNT:
        raise ProtocolError(f"0xFF8E takes 1..{bands.BAND_COUNT} ranges")
    body = bytearray()
    for index, first, length in ranges:
        band = bands.band(index)
        if length > band.channels:
            raise ProtocolError(f"band {index} has {band.channels} channels, not {length}")
        body += bytes((index, first, length))
    return command(OPCODE_BAND_RANGES, bytes((len(ranges),)) + bytes(body))


def country_codes(codes: Sequence[int]) -> bytes:
    """0xFF92: one country code per band, 0..7, or 0xFF for unavailable. [fw acib 0x1e074]"""
    if not 1 <= len(codes) <= bands.BAND_COUNT:
        raise ProtocolError(f"0xFF92 takes 1..{bands.BAND_COUNT} codes")
    for code in codes:
        if not (code < bands.BAND_COUNT or code == 0xFF):
            raise ProtocolError("a country code is below 8, or exactly 0xFF")
    return command(OPCODE_COUNTRY_CODES, bytes((len(codes),)) + bytes(codes))


def country_code(code: int) -> bytes:
    """0xFD31: one country code for the device, 0..7. [fw acib 0x1e0de]"""
    if not 0 <= code < bands.BAND_COUNT:
        raise ProtocolError("a device country code is 0..7")
    return command(OPCODE_COUNTRY_CODE, bytes((code,)))


def test_band_and_range(index: int, first: int, length: int) -> bytes:
    """0xFFB3: the test command, which refuses band 3 before anything else. [fw acib 0x18d04]"""
    if index == bands.EMPTY_BAND:
        raise ProtocolError(f"band {bands.EMPTY_BAND} is refused outright")
    band = bands.band(index)
    if length > band.channels:
        raise ProtocolError(f"band {index} has {band.channels} channels, not {length}")
    return command(OPCODE_TEST_BAND_RANGE, bytes((index, first, length)))


@dataclass(frozen=True)
class ModeChangeEvent:
    """Vendor event 0xFF with seven parameters: 00 0A, the handle, mode, result, reason.

    The three bytes are the controller's own enums: codes.Mode, codes.Result and
    codes.RejectReason. [fw acib 0x11d34]
    """

    handle: int
    mode: int
    result: int
    reason: int

    PARAM_LENGTH = 7

    def to_bytes(self) -> bytes:
        """The event packet, event code and parameter length included."""
        params = (
            EVENT_ID_MODE_CHANGE.to_bytes(2, "little")
            + self.handle.to_bytes(2, "little")
            + bytes((self.mode, self.result, self.reason))
        )
        return bytes((EVENT_VENDOR, len(params))) + params

    @classmethod
    def from_bytes(cls, packet: bytes) -> "ModeChangeEvent":
        if len(packet) < 2 or packet[0] != EVENT_VENDOR:
            raise ProtocolError("not a vendor event")
        params = packet[2:]
        if packet[1] != len(params):
            raise ProtocolError(f"event says {packet[1]} parameter bytes, carries {len(params)}")
        if len(params) != cls.PARAM_LENGTH:
            raise ProtocolError(f"the mode-change event has {cls.PARAM_LENGTH} parameters")
        if int.from_bytes(params[0:2], "little") != EVENT_ID_MODE_CHANGE:
            raise ProtocolError("not the mode-change event id")
        return cls(
            handle=int.from_bytes(params[2:4], "little"),
            mode=params[4],
            result=params[5],
            reason=params[6],
        )

    def describe(self) -> str:
        from .codes import reason_name

        def named(enum: type[Mode] | type[Result], value: int) -> str:
            try:
                return enum(value).name
            except ValueError:
                return f"0x{value:02x}"

        return (
            f"handle 0x{self.handle:04x} {named(Mode, self.mode)} "
            f"{named(Result, self.result)} {reason_name(self.reason)}"
        )
