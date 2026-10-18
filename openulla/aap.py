"""Apple's accessory protocol as the ULLA setup uses it: service 4 on L2CAP PSM 0x1001.

  PSM 0x1001                   [fw rkof 0x40e78]
  frame: u16 type, u16 service [fw rkof 0x4052c, two little-endian reads]
  types 0..4                   [fw rkof 0x405f6, a table branch over the type]
  service id 4                 [fw rkof 0x8af9e]; the sibling transport is service 0x10
  open request, 16 bytes       [fw rkof 0x4073a..0x4078a]; shorter is refused [fw 0x40600]
  open reply, 14 body bytes    [fw rkof 0x40884, 0x3f778]
  payload starts with a u16    [fw rkof 0x6cef4]; dispatch table [fw rkof 0x6cf4c]
  status reply 0x37            [fw rkof 0x6e9cc], two status values only
  wrapper 0x17                 [fw rkof 0x6cefa receiving, 0x6e198 sending]; the class field
                               is four bytes above version pair 0x00010001, two below
                               [fw rkof 0x6cf1a, 0x6e0ae]
  report bodies                type, mode, role, then data [fw rkof 0x27e90]; the two report
                               sizes are 185 and 61 [fw rkof 0x25bb2, 0x25bf4]

The session the accessory acts on is the one the L2CAP connection itself identifies: it
stores the peer address from the connection, not from any field [fw rkof 0x6ca6c, 0x4075a].
So a start command always means "with the sender", and there is no address to fill in.

No authentication appears anywhere in this exchange [measured, and fw rkof 0x40600 onwards].
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from . import octets
from .errors import ProtocolError

PSM = 0x1001
SERVICE_ULLA = 4
SERVICE_SIBLING = 0x10
HEADER_LENGTH = 4
OPEN_REQUEST_LENGTH = 16
OPEN_REPLY_BODY_LENGTH = 14
CLASS_ULLA_REPORTS = 0x00200000
REPORT_SIZES = (185, 61)  # validated against 0xB9 and 0x3D [fw rkof 0x25baa, 0x25bec]
REPORT_NOTIFICATIONS = (0xE910, 0xE911)  # the two ids those sizes belong to, in that order
REPORT_TYPES = (0, 1, 2)  # one entry into the builder each [fw rkof 0x27f42, 0x27f38, 0x27e84]
VERSION_PAIR_WIDE_CLASS = 0x00010001  # above this, the wrapper's class field is four bytes


class FrameType(IntEnum):
    OPEN = 0
    OPEN_REPLY = 1
    CONTROL_2 = 2
    CONTROL_3 = 3
    DATA = 4


class Opcode(IntEnum):
    """Service-4 opcodes this setup uses. [fw rkof 0x6cf4c]"""

    WRAPPER = 0x17
    START_SESSION = 0x36
    STATUS = 0x37
    START_STREAM = 0x38
    STOP_STREAM = 0x3A
    STOP_SESSION = 0x3C
    ABORT_RESPONSE = 0x3F
    CONFIGURE_STATISTICS = 0x43


def frame(ftype: int, service: int, body: bytes = b"") -> bytes:
    """One AAP frame: type, service, body."""
    return octets.p16(ftype, "the frame type") + octets.p16(service, "the service id") + body


@dataclass(frozen=True)
class Frame:
    ftype: int
    service: int
    body: bytes


def parse_frame(data: bytes) -> Frame:
    if len(data) < HEADER_LENGTH:
        raise ProtocolError("an AAP frame is at least four bytes")
    ftype, service = octets.u16(data, 0), octets.u16(data, 2)
    return Frame(ftype, service, data[HEADER_LENGTH:])


def open_request(service: int = SERVICE_ULLA, major: int = 1, minor: int = 2) -> bytes:
    """The 16-byte open request. The trailing eight bytes are zero in the request we send."""
    versions = octets.p16(major, "the major version") + octets.p16(minor, "the minor version")
    return frame(FrameType.OPEN, service, versions + bytes(8))


@dataclass(frozen=True)
class OpenReply:
    """Body of the open reply: a status byte, a pad, the accessory's version, eight reserved."""

    status: int
    major: int
    minor: int
    reserved: bytes = bytes(8)

    def to_bytes(self) -> bytes:
        return (
            octets.p8(self.status, "the status")
            + bytes(1)
            + octets.p16(self.major, "the major version")
            + octets.p16(self.minor, "the minor version")
            + self.reserved
        )

    @classmethod
    def from_bytes(cls, body: bytes) -> OpenReply:
        if len(body) != OPEN_REPLY_BODY_LENGTH:
            raise ProtocolError(
                f"the open reply body is {OPEN_REPLY_BODY_LENGTH} bytes, got {len(body)}"
            )
        status, major, minor = octets.u8(body, 0), octets.u16(body, 2), octets.u16(body, 4)
        return cls(status=status, major=major, minor=minor, reserved=bytes(body[6:14]))

    @property
    def ok(self) -> bool:
        return self.status == 0


def open_reply(major: int, minor: int, status: int = 0) -> bytes:
    """A full open-reply frame, as the accessory sends it."""
    return frame(FrameType.OPEN_REPLY, SERVICE_ULLA, OpenReply(status, major, minor).to_bytes())


def payload(opcode: int, body: bytes = b"") -> bytes:
    """A service-4 payload: the two-byte opcode, then its body."""
    return octets.p16(opcode, "an opcode") + body


def data_frame(opcode: int, body: bytes = b"", service: int = SERVICE_ULLA) -> bytes:
    """A type-4 frame carrying one command."""
    return frame(FrameType.DATA, service, payload(opcode, body))


def parse_payload(body: bytes) -> tuple[int, bytes]:
    if len(body) < 2:
        raise ProtocolError("a service-4 payload starts with a two-byte opcode")
    return octets.u16(body, 0), body[2:]


def start_session(mode: int = 0) -> bytes:
    """0x36: start a ULLA session with the sender. One mode byte. [fw rkof 0x6d602]"""
    if not 0 <= mode <= 0xFF:
        raise ProtocolError("the mode is one byte")
    return data_frame(Opcode.START_SESSION, bytes((mode,)))


def start_stream() -> bytes:
    """0x38: start the audio stream. [fw rkof 0x6d55a]"""
    return data_frame(Opcode.START_STREAM)


def stop_stream() -> bytes:
    """0x3A: stop the audio stream. [fw rkof 0x6dae4]"""
    return data_frame(Opcode.STOP_STREAM)


def stop_session(value: int = 0) -> bytes:
    """0x3C: stop the session. Four body bytes. [fw rkof 0x6d396]"""
    return data_frame(Opcode.STOP_SESSION, octets.p32(value, "the stop value"))


def abort_response(value: int = 0) -> bytes:
    """0x3F: abort response. Four body bytes. [fw rkof 0x6daaa]"""
    return data_frame(Opcode.ABORT_RESPONSE, octets.p32(value, "the abort value"))


def configure_statistics(enable: int, first: int, second: int) -> bytes:
    """0x43: configure statistics, one byte and two words. [fw rkof 0x6d2e0]"""
    return data_frame(
        Opcode.CONFIGURE_STATISTICS,
        octets.p8(enable, "the enable byte") + octets.p32(first) + octets.p32(second),
    )


def status_reply(status: int) -> bytes:
    """0x37 with its four-byte status. [fw rkof 0x6e9cc]"""
    return data_frame(Opcode.STATUS, octets.p32(status, "the status"))


def parse_status(body: bytes) -> int:
    opcode, rest = parse_payload(body)
    if opcode != Opcode.STATUS:
        raise ProtocolError(f"opcode 0x{opcode:02x} is not the status reply")
    if len(rest) != 4:
        raise ProtocolError(f"the status reply carries four bytes, got {len(rest)}")
    return int.from_bytes(rest, "little")


def class_width(version_pair: int) -> int:
    """How many bytes the wrapper's class field takes. [fw rkof 0x6cf1a, 0x6e0ae]"""
    return 4 if version_pair > VERSION_PAIR_WIDE_CLASS else 2


def wrapper(cls_: int, body: bytes = b"", width: int = 4) -> bytes:
    """Opcode 0x17: a class, a length, and a payload. [fw rkof 0x6e198]"""
    if width not in (2, 4):
        raise ProtocolError("the class field is two or four bytes")
    if cls_ >= 1 << (8 * width):
        raise ProtocolError(f"class 0x{cls_:x} does not fit in {width} bytes")
    return data_frame(
        Opcode.WRAPPER,
        cls_.to_bytes(width, "little") + octets.p16(len(body), "the payload length") + body,
    )


@dataclass(frozen=True)
class Wrapper:
    cls_: int
    body: bytes


def parse_wrapper(body: bytes, width: int = 4) -> Wrapper:
    opcode, rest = parse_payload(body)
    if opcode != Opcode.WRAPPER:
        raise ProtocolError(f"opcode 0x{opcode:02x} is not the wrapper")
    if len(rest) < width + 2:
        raise ProtocolError("the wrapper needs a class and a length")
    cls_ = int.from_bytes(rest[:width], "little")
    length = octets.u16(rest, width)
    payload_bytes = rest[width + 2 :]
    if len(payload_bytes) != length:
        raise ProtocolError(
            f"the wrapper says {length} payload bytes, carries {len(payload_bytes)}"
        )
    return Wrapper(cls_, payload_bytes)


def subscribe_reports(cls_: int = CLASS_ULLA_REPORTS, width: int = 4) -> bytes:
    """Subscribe to the ULLA report streams. Class 0x00200000. [fw rkof 0x6d116, 0x27f60]

    The firmware never reads the payload of this one [fw rkof 0x27f60], but its receive path
    allocates by the length, so four bytes are sent.
    """
    return wrapper(cls_, bytes(4), width)


@dataclass(frozen=True)
class Report:
    """A report body: its type, then two bytes the manager copies from its own object.

    Those two are the manager's fields at its offsets 0 and 0x20 [fw rkof 0x27eba..0x27ed0];
    what they mean is not established, and the manager's current state is a different field.
    The sibling gets the same report with a four-byte header whose first byte is 4
    [fw rkof 0x27ef4..0x27f16].
    """

    report_type: int
    manager_fields: tuple[int, int]
    data: bytes

    SIBLING_MARKER = 4


def parse_report(body: bytes, sibling: bool = False) -> Report:
    """Read a report body, either the host's three-byte header or the sibling's four."""
    header_length = 4 if sibling else 3
    if len(body) < header_length:
        raise ProtocolError(f"a report body is at least {header_length} bytes")
    if sibling:
        if body[0] != Report.SIBLING_MARKER:
            raise ProtocolError(f"a sibling report starts with {Report.SIBLING_MARKER}")
        body = body[1:]
    return Report(body[0], (body[1], body[2]), body[3:])
