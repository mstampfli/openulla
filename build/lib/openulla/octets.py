"""Reading and writing the integers this protocol uses, in one place.

Every multi-byte integer in the setup protocol is little-endian [docs/protocol.md, the note
under section 1], with one exception that is deliberate and documented: the six address bytes
of the sibling's start-session request travel most significant byte first
[fw rkof 0x27466, and openulla.sibling]. The capture containers are the other way round, so
the big-endian readers are here too and say which they are.

These exist instead of struct so that no value in this package has the type `Any`: struct's
unpack returns a tuple of `Any`, which is exactly the hole a protocol decoder must not have.
"""
from __future__ import annotations

from .errors import ProtocolError


def _slice(data: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or offset + size > len(data):
        raise ProtocolError(f"need {size} bytes at offset {offset}, have {len(data)}")
    return data[offset : offset + size]


def u8(data: bytes, offset: int = 0) -> int:
    return _slice(data, offset, 1)[0]


def u16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(_slice(data, offset, 2), "little")


def u32(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(_slice(data, offset, 4), "little")


def be16(data: bytes, offset: int = 0) -> int:
    """Big-endian, for a capture container's own headers."""
    return int.from_bytes(_slice(data, offset, 2), "big")


def be32(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(_slice(data, offset, 4), "big")


def be64(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(_slice(data, offset, 8), "big")


def _bytes(value: int, size: int, name: str) -> bytes:
    if not 0 <= value < 1 << (8 * size):
        raise ProtocolError(f"{name} does not fit in {size} byte{'s' if size > 1 else ''}")
    return value.to_bytes(size, "little")


def p8(value: int, name: str = "a byte") -> bytes:
    return _bytes(value, 1, name)


def p16(value: int, name: str = "a halfword") -> bytes:
    return _bytes(value, 2, name)


def p32(value: int, name: str = "a word") -> bytes:
    return _bytes(value, 4, name)
