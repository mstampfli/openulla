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


def test_band_ranges_carry_the_triples_with_no_count_byte():
    pdu = lmp.BandRanges((lmp.BandRange(5, 29, 6), lmp.BandRange(7, 41, 7)))
    raw = pdu.to_bytes()
    assert raw == bytes.fromhex("f6 1c 05 1d 06 07 29 07")
    assert len(raw) == 2 + 3 * 2
    assert lmp.BandRanges.from_bytes(raw).ranges == pdu.ranges
    with pytest.raises(ProtocolError):
        lmp.BandRanges(tuple(lmp.BandRange(1, 13, 5) for _ in range(6))).to_bytes()


def test_the_count_is_the_length_and_nothing_else():
    """Both ends keep the count in their own block and derive it from the length. [6.5]"""
    for n in range(0, lmp.MAX_BAND_RANGES + 1):
        raw = lmp.BandRanges(tuple(lmp.BandRange(1, 13, 5) for _ in range(n))).to_bytes()
        assert len(lmp.BandRanges.from_bytes(raw).ranges) == n


def test_a_length_that_is_not_a_whole_number_of_triples_loses_its_tail():
    """The firmware divides, so the remainder is ignored rather than refused."""
    parsed = lmp.BandRanges.from_bytes(bytes.fromhex("f6 1c 05 1d 06 07 29"))
    assert len(parsed.ranges) == 1 and parsed.ignored == bytes.fromhex("07 29")


def test_more_than_five_triples_are_refused_by_length():
    body = b"".join(bytes((i, 0, 0)) for i in range(6))
    with pytest.raises(ProtocolError):
        lmp.BandRanges.from_bytes(bytes.fromhex("f6 1c") + body)


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


def test_the_feature_exchange_carries_one_word_each_way():
    request = lmp.Features(0x000000E0)
    assert request.to_bytes() == bytes.fromhex("f6 0a e0 00 00 00")
    assert lmp.Features.from_bytes(request.to_bytes()) == request
    reply = lmp.Features(0x12345678, reply=True, tid=1)
    assert reply.to_bytes() == bytes.fromhex("f7 0b 78 56 34 12")
    assert lmp.parse(reply.to_bytes()) == reply
    assert lmp.Features(0, reply=True).ext_opcode == lmp.EXT_FEATURE_REPLY
    with pytest.raises(ProtocolError):
        lmp.Features.from_bytes(bytes.fromhex("f6 0a e0 00 00"))


def test_a_feature_is_usable_only_when_both_ends_carry_it():
    bands_bit = 1 << lmp.FEATURE_BIT_BANDS
    assert lmp.agreed(bands_bit, bands_bit, lmp.FEATURE_BIT_BANDS)
    assert not lmp.agreed(bands_bit, 0, lmp.FEATURE_BIT_BANDS)
    assert not lmp.agreed(0, bands_bit, lmp.FEATURE_BIT_BANDS)
    assert not lmp.agreed(bands_bit, bands_bit, lmp.FEATURE_BIT_MODE_PROCEDURE)
    with pytest.raises(ProtocolError):
        lmp.agreed(1, 1, 8)


def test_the_mode_procedure_gate_takes_either_of_its_two_bytes():
    bit = 1 << lmp.FEATURE_BIT_MODE_PROCEDURE
    assert lmp.mode_procedure_agreed(bit, bit)
    assert lmp.mode_procedure_agreed(bit << 24, bit << 24)
    assert not lmp.mode_procedure_agreed(bit, bit << 24)
    assert not lmp.mode_procedure_agreed(0, 0xFFFFFFFF)


def test_a_quirked_link_keeps_the_band_bit_in_what_it_advertises():
    assert lmp.FEATURE_REDUCED_LOW_BYTE & (1 << lmp.FEATURE_BIT_BANDS)
    assert not lmp.FEATURE_REDUCED_LOW_BYTE & (1 << lmp.FEATURE_BIT_MODE_PROCEDURE)


def test_the_two_messages_that_follow_the_configuration():
    """0x75 has 0x74's layout; 0x76 is the header alone. [protocol.md 5.2]"""
    alt = lmp.Configuration(0x11223344, 0x1D4B, opcode=lmp.OPCODE_ULLA_CONFIG_ALT)
    raw = alt.to_bytes()
    assert raw == bytes.fromhex("ea 44 33 22 11 4b 1d")
    assert len(raw) == lmp.Configuration.LENGTH
    assert lmp.Configuration.from_bytes(raw) == alt
    assert lmp.parse(raw).opcode == lmp.OPCODE_ULLA_CONFIG_ALT
    bare = lmp.SessionBare(tid=1)
    assert bare.to_bytes() == bytes((0x76 << 1 | 1,))
    assert lmp.parse(bare.to_bytes()) == bare
    with pytest.raises(ProtocolError):
        lmp.SessionBare.from_bytes(bytes((0x76 << 1, 0x00)))


def test_the_bound_that_0x75s_receiver_applies():
    assert lmp.Configuration.MAX_VALUE16_FOR_75 == 7499
    inside = lmp.Configuration(0, 7499, opcode=lmp.OPCODE_ULLA_CONFIG_ALT)
    outside = lmp.Configuration(0, 7500, opcode=lmp.OPCODE_ULLA_CONFIG_ALT)
    assert not inside.would_be_dropped()
    assert outside.would_be_dropped()
    # The bound belongs to 0x75 alone: 0x74's receiver checks nothing.
    assert not lmp.Configuration(0, 0xFFFF).would_be_dropped()


def test_an_opcode_that_is_not_a_configuration_is_refused():
    with pytest.raises(ProtocolError):
        lmp.Configuration(0, 0, opcode=0x77).to_bytes()
