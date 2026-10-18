"""The link-manager PDUs: headers, lengths and the one rule that is not what it looks like."""
import pytest

from openulla import lmp
from openulla.errors import ProtocolError


def test_the_header_byte_carries_the_opcode_and_the_transaction_id():
    assert lmp.header(0x73, 0) == 0xE6
    assert lmp.header(0x73, 1) == 0xE7
    head = lmp.parse_header(bytes((0xE7, 0)))
    assert (head.opcode, head.tid) == (0x73, 1)


def test_an_extended_opcode_follows_only_from_seven_b_upwards():
    assert lmp.parse_header(bytes((0x7A << 1, 0x17))).ext_opcode is None
    assert lmp.parse_header(bytes((0x7A << 1, 0x17))).body_offset == 1
    assert lmp.parse_header(bytes((0x7B << 1, 0x17))).ext_opcode == 0x17
    assert lmp.parse_header(bytes((0x7F << 1, 0x17))).ext_opcode == 0x17
    with pytest.raises(ProtocolError):
        lmp.parse_header(bytes((0x7B << 1,)))


def test_the_setup_request_is_nine_bytes_and_round_trips():
    pdu = lmp.SetupRequest(0x11223344, 0xABCD, 0x03, 0x01, tid=1)
    raw = pdu.to_bytes()
    assert len(raw) == lmp.SetupRequest.LENGTH == 9
    assert raw == bytes.fromhex("e7 44 33 22 11 cd ab 03 01")
    assert lmp.SetupRequest.from_bytes(raw) == pdu
    assert isinstance(lmp.parse(raw), lmp.SetupRequest)


def test_the_setup_request_refuses_a_length_that_is_not_nine():
    with pytest.raises(ProtocolError):
        lmp.SetupRequest.from_bytes(bytes.fromhex("e6 44 33 22 11 cd ab 03 01 00"))
    with pytest.raises(ProtocolError):
        lmp.SetupRequest.from_bytes(bytes.fromhex("e6 44 33 22 11 cd ab 03"))


def test_the_configuration_is_seven_bytes_and_round_trips():
    pdu = lmp.Configuration(0xDEADBEEF, 0x1234)
    raw = pdu.to_bytes()
    assert len(raw) == lmp.Configuration.LENGTH == 7
    assert raw == bytes.fromhex("e8 ef be ad de 34 12")
    assert lmp.Configuration.from_bytes(raw) == pdu


def test_accepted_and_not_accepted_use_the_standard_opcodes():
    assert lmp.Accepted(0x73).to_bytes() == bytes.fromhex("06 73")
    assert lmp.NotAccepted(0x73, 0x85).to_bytes() == bytes.fromhex("08 73 85")
    assert lmp.parse(bytes.fromhex("06 73")) == lmp.Accepted(0x73)
    assert lmp.parse(bytes.fromhex("08 73 85")) == lmp.NotAccepted(0x73, 0x85)


def test_the_allowed_bands_pdu_is_the_escape_the_mask_and_nothing_else():
    assert lmp.AllowedBands(0xF0).to_bytes() == bytes.fromhex("f6 17 f0")
    assert lmp.AllowedBands.from_bytes(bytes.fromhex("f7 17 03")) == lmp.AllowedBands(3, tid=1)
    with pytest.raises(ProtocolError):
        lmp.AllowedBands.from_bytes(bytes.fromhex("f6 17 f0 00"))


def test_the_same_extended_opcode_in_the_other_escape_space_is_not_it():
    with pytest.raises(ProtocolError):
        lmp.AllowedBands.from_bytes(bytes.fromhex("fe 17 f0"))


def test_band_ranges_carry_up_to_five_triples():
    pdu = lmp.BandRanges((lmp.BandRange(5, 29, 6), lmp.BandRange(7, 41, 7)))
    raw = pdu.to_bytes()
    assert raw == bytes.fromhex("f6 1c 02 05 1d 06 07 29 07")
    assert lmp.BandRanges.from_bytes(raw).ranges == pdu.ranges
    with pytest.raises(ProtocolError):
        lmp.BandRanges(tuple(lmp.BandRange(1, 13, 5) for _ in range(6))).to_bytes()


def test_the_receiver_recomputes_the_count_instead_of_trusting_it():
    """The count byte says four; the body holds two triples, and two is what is read."""
    lying = bytes.fromhex("f6 1c 04 05 1d 06 07 29 07")
    parsed = lmp.BandRanges.from_bytes(lying)
    assert parsed.count_byte == 4
    assert len(parsed.ranges) == 2


def test_more_than_five_triples_are_refused_by_length():
    body = b"".join(bytes((i, 0, 0)) for i in range(6))
    with pytest.raises(ProtocolError):
        lmp.BandRanges.from_bytes(bytes.fromhex("f6 1c 06") + body)


def test_an_unknown_opcode_is_reported_rather_than_guessed():
    with pytest.raises(ProtocolError):
        lmp.parse(bytes((0x20, 0x00)))


def test_the_escape_spaces_know_what_this_build_implements():
    """The two sets partition each space's extended opcodes. [protocol.md 5.6]"""
    assert lmp.ESCAPE_7B_IMPLEMENTED | lmp.ESCAPE_7B_REFUSED == set(lmp.ESCAPE_7B_EXT)
    assert not lmp.ESCAPE_7B_IMPLEMENTED & lmp.ESCAPE_7B_REFUSED
    assert len(lmp.ESCAPE_7B_REFUSED) == 12 and len(lmp.ESCAPE_7B_IMPLEMENTED) == 27
    assert lmp.ESCAPE_7F_IMPLEMENTED | lmp.ESCAPE_7F_REFUSED == set(lmp.ESCAPE_7F_EXT)
    assert len(lmp.ESCAPE_7F_REFUSED) == 8 and len(lmp.ESCAPE_7F_IMPLEMENTED) == 26


def test_the_two_messages_we_encode_are_ones_the_earbud_dispatches():
    assert lmp.implemented(lmp.ESCAPE_7B, lmp.EXT_ALLOWED_BANDS)
    assert lmp.implemented(lmp.ESCAPE_7B, lmp.EXT_BAND_RANGES)


def test_the_two_the_earbud_sends_but_refuses():
    assert not lmp.implemented(lmp.ESCAPE_7B, 0x24)  # on the reject stub
    assert 0x28 not in lmp.ESCAPE_7B_EXT  # past the end of the table altogether
    assert not lmp.implemented(lmp.ESCAPE_7B, 0x28)


def test_an_opcode_that_is_not_an_escape_has_no_space():
    with pytest.raises(ProtocolError):
        lmp.implemented(0x73, 1)
