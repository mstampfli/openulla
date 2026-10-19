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

from collections.abc import Sequence
from dataclasses import dataclass

from . import bands, octets
from .codes import HciError, Mode, Result
from .errors import ProtocolError

OGF_VENDOR = 0x3F
H4_COMMAND = 0x01
H4_EVENT = 0x04
EVENT_VENDOR = 0xFF

OPCODE_ULLA_SETUP = 0xFE50
OPCODE_ULLA_SECOND = 0xFE51  # handle, a zero byte, then the manager's mode field
# The constants the earbud's own manager puts in 0xFE50's parameters [fw rkof 0x272c0], with
# the flags byte chosen by one comparison on its stored mode field [fw rkof 0x271c4 onwards].
MANAGER_EXTRA_FLAGS = 1
MANAGER_CONFIG_WORD = 0x0096
MANAGER_FLAGS_MODE_ONE = 0x04
MANAGER_FLAGS_OTHERWISE = 0x0F
OPCODE_FEATURE_GROUP = 0xFDF3  # four bits of the feature byte, bit 6 among them
ULLA_SESSION_COMMANDS: dict[int, int] = {
    0xFE16: 0x1EBF4,
    0xFE4C: 0x1D6CC,
    0xFE4E: 0x1E7B8,
    0xFE50: 0x1DA40,
    0xFE52: 0x1DD88,
    0xFE53: 0x1DE62,
    0xFE55: 0x1DDE4,
    0xFE57: 0x1D900,
    0xFE58: 0x1D952,
    0xFE7B: 0x1D44E,
    0xFF79: 0x193F4,
}
"""Every vendor command whose handler reaches the ULLA session object, and its handler.

Found mechanically, not guessed: the session has one accessor [fw acib 0x2f52c] and these are
the command handlers that call it. Five of them share one gate chain and answer 0x0C when the
session is not active; what each means is not traced. protocol.md 6.6.
"""
SESSION_COMMAND_REFUSAL = 0x0C  # what the gated ones answer when the session is not active
FE16_VALUE_RANGE = (0x7D, 0x2710)
"""The bound 0xFE16's 32-bit parameter has to be inside, or the command answers 0x11.

125 to 10000 inclusive [fw acib 0x1ec00 and 0x1ec04]. What it measures is not traced, so the
range is recorded and not interpreted. protocol.md 6.6.
"""
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

    `mode` is the last parameter, and it chooses the handler's path [fw acib 0x1daa2]. With 0
    the transition runs: `extra_flags` is copied into the last byte of the setup request PDU,
    `config_word` into its configuration word, and bits 0, 1 and 3 of `flags` into its flag
    byte, with bit 2 used elsewhere [fw acib 0x1dace..0x1db0a]. With anything else a different
    procedure runs, and there `flags` is not a bitmask but a value: 5 or above is refused with
    0x12, and 4 is its one special value [fw acib 0x1daa6, 0x1db7e, 0x1db92].

    The byte that reaches this parameter in the earbud's own source-role code is the mode byte
    of AAP opcode 0x36; see hci.manager_setup and docs/protocol.md 6.0.
    """

    handle: int
    extra_flags: int
    config_word: int
    flags: int
    mode: int = 0

    LENGTH = 7
    MAX_PROCEDURE_FLAGS = 4  # above this the procedure path answers 0x12

    def refusal(self) -> int | None:
        """The error the controller would answer for these parameters, if any.

        Only what is proven is modelled: the procedure path's range check. The feature bits
        that can also produce 0x0C, and the busy state behind 0x17, are per-device runtime
        state this cannot know [fw acib 0x1db5e..0x1db7c, 0x1da6c].
        """
        if self.mode != 0 and self.flags > self.MAX_PROCEDURE_FLAGS:
            return HciError.INVALID_PARAMETERS
        return None

    def to_bytes(self) -> bytes:
        if not 0 <= self.handle <= 0x0EFF:
            raise ProtocolError("a connection handle is twelve bits")
        return (
            octets.p16(self.handle, "the connection handle")
            + octets.p8(self.extra_flags, "extra_flags")
            + octets.p16(self.config_word, "config_word")
            + octets.p8(self.flags, "flags")
            + octets.p8(self.mode, "mode")
        )

    @classmethod
    def from_bytes(cls, params: bytes) -> UllaSetup:
        if len(params) != cls.LENGTH:
            raise ProtocolError(f"0xFE50 takes {cls.LENGTH} parameter bytes, got {len(params)}")
        return cls(
            handle=int.from_bytes(params[0:2], "little"),
            extra_flags=params[2],
            config_word=int.from_bytes(params[3:5], "little"),
            flags=params[5],
            mode=params[6],
        )

    def command(self) -> bytes:
        return command(OPCODE_ULLA_SETUP, self.to_bytes())


def manager_setup(handle: int, mode: int) -> UllaSetup:
    """0xFE50 with the parameters the earbud's own manager uses. [fw rkof 0x272c0, 0x271c4]

    `mode` is the byte the manager stores and passes on: the mode of AAP opcode 0x36, which is
    the last parameter here and the last byte of the sibling start-session request.

    Mode 2 is worth a word: the manager pairs it with the flag byte 0xF, which the procedure
    path refuses with 0x12, and `UllaSetup.refusal` says so. That is the firmware's own
    pairing, in code the earbud never reaches; docs/protocol.md 6.0 has the table.
    """
    return UllaSetup(
        handle=handle,
        extra_flags=MANAGER_EXTRA_FLAGS,
        config_word=MANAGER_CONFIG_WORD,
        flags=MANAGER_FLAGS_MODE_ONE if mode == 1 else MANAGER_FLAGS_OTHERWISE,
        mode=mode,
    )


def ulla_second_command(handle: int, mode_field: int) -> bytes:
    """0xFE51: the handle, a zero byte and the same mode field. [fw rkof 0x27234]"""
    if not 0 <= handle <= 0x0EFF:
        raise ProtocolError("a connection handle is twelve bits")
    if not 0 <= mode_field <= 0xFF:
        raise ProtocolError("the mode field is one byte")
    return command(OPCODE_ULLA_SECOND, handle.to_bytes(2, "little") + bytes((0, mode_field)))


def band_gate(enable: bool) -> bytes:
    """0xFF77: the switch that gates every other band command. [fw acib 0x192dc]

    Its one byte sets or clears **bit 5** of the controller's own feature word, which is the
    bit every band message is gated on [fw acib 0x192f4 to 0x19306, and the gate at 0x29916].
    So this command is not a convenience: without it the controller's own half of that AND
    is zero. 0xFF78 refuses outright while the bit is clear [fw acib 0x19348].
    """
    return command(OPCODE_BAND_GATE, bytes((1 if enable else 0,)))


def session_command(opcode: int, params: bytes = b"") -> bytes:
    """One of the commands of protocol.md 6.6, with the payload the caller knows.

    Only the opcodes whose handlers reach the ULLA session object are allowed, so a typo
    cannot turn into a command this document has never looked at. The two payloads the
    earbud's own manager uses are no bytes at all for 0xFE52 and four zero bytes for 0xFE58
    [fw rkof 0x26940, 0x26866]; what the others take is not traced.
    """
    if opcode not in ULLA_SESSION_COMMANDS:
        raise ProtocolError(f"0x{opcode:04x} is not one of the ULLA session commands")
    return command(opcode, params)


def feature_group(enable: bool) -> bytes:
    """0xFDF3: sets or clears the other four bits of that feature byte. [fw acib 0x16b20]

    The byte splits into two groups: bits 0, 3, 5 and 7 survive this command either way (the
    mask 0xA9) and bits 1, 2, 4 and 6 are the ones it adds (0x56). Bit 6 is the gate on
    0xFE50's mode procedure and bits 1 and 2 steer the reply of protocol.md 5.11, so a source
    that wants the mode procedure has to send this one as well as 0xFF77
    [fw acib 0x16b28 and 0x16b2e, the gates at 0x1db5e and 0x29204].
    """
    return command(OPCODE_FEATURE_GROUP, bytes((1 if enable else 0,)))


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
    def from_bytes(cls, packet: bytes) -> ModeChangeEvent:
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
