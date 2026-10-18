"""The integer readers and writers, including the endianness this protocol uses."""
import pytest

from openulla import octets
from openulla.errors import ProtocolError

FRAME = bytes.fromhex("01 02 03 04 05 06 07 08")


def test_little_endian_is_what_the_protocol_uses():
    assert octets.u8(FRAME) == 0x01
    assert octets.u16(FRAME) == 0x0201
    assert octets.u32(FRAME) == 0x04030201
    assert octets.u16(FRAME, 2) == 0x0403
    assert octets.u32(FRAME, 4) == 0x08070605


def test_the_big_endian_readers_are_for_a_capture_container():
    assert octets.be16(FRAME) == 0x0102
    assert octets.be32(FRAME) == 0x01020304
    assert octets.be64(FRAME) == 0x0102030405060708


def test_a_short_buffer_is_refused_the_same_way_as_any_malformed_frame():
    for reader in (octets.u16, octets.u32, octets.be16, octets.be32, octets.be64):
        with pytest.raises(ProtocolError):
            reader(b"\x01")
    with pytest.raises(ProtocolError):
        octets.u8(b"")
    with pytest.raises(ProtocolError):
        octets.u16(FRAME, 7)
    with pytest.raises(ProtocolError):
        octets.u16(FRAME, -1)


def test_writing_checks_the_range_and_names_the_field():
    assert octets.p8(0xFF) == b"\xff"
    assert octets.p16(0x0201) == b"\x01\x02"
    assert octets.p32(0x04030201) == b"\x01\x02\x03\x04"
    for writer, too_big in ((octets.p8, 0x100), (octets.p16, 0x10000), (octets.p32, 1 << 32)):
        with pytest.raises(ProtocolError) as refused:
            writer(too_big, "the field")
        assert "the field" in str(refused.value)
        with pytest.raises(ProtocolError):
            writer(-1)


def test_what_is_written_reads_back():
    for value in (0, 1, 0x7F, 0xFF):
        assert octets.u8(octets.p8(value)) == value
    for value in (0, 1, 0x1234, 0xFFFF):
        assert octets.u16(octets.p16(value)) == value
    for value in (0, 1, 0xDEADBEEF, 0xFFFFFFFF):
        assert octets.u32(octets.p32(value)) == value
