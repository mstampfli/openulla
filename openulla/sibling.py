"""The earbud-to-earbud messages: client 5 of the transport carried over AAP service 0x10.

  transport, client 5     [fw rkof 0x27692, 0x29e0e, 0x2a624]
  frame: client, message  [fw rkof 0x2aee0, the one sender every message goes through]
  message ids 1..13       every sender located among the calls to 0x2aee0
  address order           the start-session request packs the six address bytes most
                          significant first; the receiver reverses them back into the order
                          the Bluetooth stack uses [fw rkof 0x30978, 0x2b37a]

Every sender first checks that the sibling transport is in an active mode and connected.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from .errors import ProtocolError

SERVICE = 0x10
CLIENT_SESSION_MANAGER = 5
ADDRESS_LENGTH = 6


class Message(IntEnum):
    """The session manager's messages to its sibling. [fw rkof calls to 0x2aee0]"""

    START_SESSION_REQ = 1
    START_STREAM_REQ = 2
    STOP_SESSION_REQ = 3
    STOP_STREAM_REQ = 4
    START_SESSION_RSP = 5
    START_STREAM_RSP = 6
    STOP_SESSION_RSP = 7
    STOP_STREAM_RSP = 8
    DSP_CONFIG_COMPLETE = 9
    SESSION_STOPPED = 10
    SOURCE_DISCONNECTED = 11
    STATISTICS = 12
    CONFIGURE_STATISTICS = 13


EMPTY_BODY = frozenset(
    {
        Message.START_STREAM_REQ,
        Message.STOP_SESSION_REQ,
        Message.STOP_STREAM_REQ,
        Message.START_SESSION_RSP,
        Message.START_STREAM_RSP,
        Message.STOP_SESSION_RSP,
        Message.STOP_STREAM_RSP,
        Message.DSP_CONFIG_COMPLETE,
        Message.SESSION_STOPPED,
        Message.SOURCE_DISCONNECTED,
    }
)


def frame(message: int, body: bytes = b"", client: int = CLIENT_SESSION_MANAGER) -> bytes:
    """One transport frame: the client id, the message id, then the body."""
    if not 0 <= client <= 0xFF or not 0 <= message <= 0xFF:
        raise ProtocolError("the client and message ids are one byte each")
    return bytes((client, message)) + body


@dataclass(frozen=True)
class SiblingFrame:
    client: int
    message: int
    body: bytes


def parse_frame(data: bytes) -> SiblingFrame:
    if len(data) < 2:
        raise ProtocolError("a sibling frame is at least two bytes")
    return SiblingFrame(data[0], data[1], data[2:])


def start_session_request(address: bytes, mode: int) -> bytes:
    """Message 1: the source's address, most significant byte first, then the mode.

    `address` is given in the order the Bluetooth stack uses, least significant byte first,
    the same order HCI and BlueZ use; this packs it the other way round, as the firmware does.
    """
    if len(address) != ADDRESS_LENGTH:
        raise ProtocolError("a Bluetooth address is six bytes")
    if not 0 <= mode <= 0xFF:
        raise ProtocolError("the mode is one byte")
    return frame(Message.START_SESSION_REQ, bytes(reversed(address)) + bytes((mode,)))


@dataclass(frozen=True)
class StartSessionRequest:
    address: bytes
    mode: int


def parse_start_session_request(data: bytes) -> StartSessionRequest:
    """Read message 1 back, reversing the address as the receiving side does."""
    parsed = parse_frame(data)
    if parsed.message != Message.START_SESSION_REQ:
        raise ProtocolError(f"message {parsed.message} is not the start-session request")
    if len(parsed.body) != ADDRESS_LENGTH + 1:
        raise ProtocolError("the start-session request is six address bytes and a mode")
    return StartSessionRequest(bytes(reversed(parsed.body[:ADDRESS_LENGTH])), parsed.body[6])


def configure_statistics(enable: int, first: int, second: int) -> bytes:
    """Message 13: the host's configure-statistics body, passed through unchanged."""
    return frame(Message.CONFIGURE_STATISTICS, struct.pack("<BII", enable, first, second))


def statistics(report: bytes) -> bytes:
    """Message 12: the report buffer, as it reaches the host in the 0x17 wrapper."""
    return frame(Message.STATISTICS, report)


def empty(message: int) -> bytes:
    """Any of the messages whose body is empty."""
    if message not in EMPTY_BODY:
        raise ProtocolError(f"message {message} carries a body")
    return frame(message)
