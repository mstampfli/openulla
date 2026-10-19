"""The controller's packet-type table, and what identifies the six entries in table 2.

Every number here is checked against a firmware image by `tools/verify_claims.py`. What this
file checks is the reasoning on top of the numbers: that the twelve specified entries really are
the specification's, that the TYPE codes make table 2 a mirror of table 1, and that the rate
derivation is consistent rather than chosen to fit.
"""
import pytest

from openulla import lmp, packets


def test_the_twelve_specified_payload_maxima_are_all_present():
    """The control that identifies the table at all [protocol.md 5.13]."""
    present = {entry.payload for entry in packets.TABLE}
    assert set(packets.SPECIFIED_PAYLOADS) <= present
    extra = sorted(present - set(packets.SPECIFIED_PAYLOADS))
    assert extra == [112, 242, 703, 1288, 1416, 2586]
    assert len(packets.TABLE) == 18


def test_no_two_entries_of_one_table_share_a_type_code():
    """Why each entry's table membership is known: a TYPE code means one packet per table."""
    for table in (0, 1, 2):
        codes = [entry.type_code for entry in packets.for_table(table)]
        assert len(codes) == len(set(codes)), table


def test_table_2_has_exactly_table_1s_type_code_layout():
    """The strongest evidence for the slot and rate readings, and it needs no arithmetic.

    In table 1 the codes 0x4 and 0x8 are the one-slot pair, 0xA and 0xB the three-slot pair and
    0xE and 0xF the five-slot pair, the lower code being the lower rate in each. Table 2 uses
    the same seven codes, so each one's slot count and which of its pair is faster carry over.
    """
    def layout(table):
        return {entry.type_code: entry for entry in packets.for_table(table)}

    one, two = layout(1), layout(2)
    assert set(one) == set(two) == {0x3, 0x4, 0x8, 0xA, 0xB, 0xE, 0xF}
    for code in one:
        assert one[code].slots == two[code].slots, code
    # In both tables the lower code of each pair carries the smaller payload, so it is the
    # slower packet; that is what carries "which of this pair is HDR4" across from table 1.
    for lower, higher in ((0x4, 0x8), (0xA, 0xB), (0xE, 0xF)):
        for table in (one, two):
            assert table[lower].payload < table[higher].payload
            assert table[lower].slots == table[higher].slots


def test_the_rates_follow_from_the_payloads():
    """At 4 and 8 Mb/s each slot length agrees, the way the specified entries do at 1, 2 and 3."""
    def window(name, rate):
        entry = next(e for e in packets.TABLE if e.name == name)
        return packets.payload_window_us(entry, rate)

    # The control: the specified five-slot entries all land near 2.72 ms.
    assert 2700 < window("DH5", 1) < 2730
    assert 2700 < window("2-DH5", 2) < 2730
    assert 2700 < window("3-DH5", 3) < 2730
    # The six extra ones land near 2.58 ms for five slots and 1.41 ms for three, at 4 and 8.
    assert 2570 < window("apple_4mbit_5slot", 4) < 2590
    assert 2570 < window("apple_8mbit_5slot", 8) < 2590
    assert 1400 < window("apple_4mbit_3slot", 4) < 1420
    assert 1400 < window("apple_8mbit_3slot", 8) < 1420
    # And the two rates of one slot length stand in a ratio of about two.
    for slots in (3, 5):
        low = next(e for e in packets.TABLE if e.slots == slots and "4mbit" in e.name)
        high = next(e for e in packets.TABLE if e.slots == slots and "8mbit" in e.name)
        assert 2.0 <= high.payload / low.payload < 2.02
    assert packets.APPLE_RATES == (4, 8)
    with pytest.raises(ValueError):
        packets.payload_window_us(packets.TABLE[0], 0)


def test_the_chooser_reproduces_what_a_standard_host_can_ask_for():
    """Given only the sixteen-bit field, the best it can pick is 3-DH5 [measurements.md 9]."""
    assert packets.choose(0xFFFF).name == "3-DH5"
    assert packets.choose(0xFFFFFFFF).name == "apple_8mbit_5slot"
    assert packets.choose(1 << 3).name == "DM1"
    assert packets.choose(0) is None
    assert packets.by_mask_bit(21).payload == 2586
    with pytest.raises(ValueError):
        packets.by_mask_bit(31)


def test_apples_request_admits_one_more_table_than_the_specifications():
    """The one difference between the two forms of the same request [protocol.md 5.13]."""
    assert lmp.PacketTableRequest(table=2).to_bytes() == bytes((0xF6, 0x0D, 0x02))
    assert lmp.parse(bytes((0xF6, 0x0D, 0x02))) == lmp.PacketTableRequest(table=2)
    assert lmp.PacketTableRequest(table=2).selectable() == packets.for_table(2)
    with pytest.raises(lmp.ProtocolError):
        lmp.PacketTableRequest(table=3).to_bytes()
    # On the wire, the firmware answers 0x1E rather than ignoring it.
    with pytest.raises(lmp.ProtocolError) as refused:
        lmp.parse(bytes((0xF6, 0x0D, 0x03)))
    assert refused.value.code == 0x1E
    with pytest.raises(lmp.ProtocolError):
        lmp.PacketTableRequest.from_bytes(bytes((0xF6, 0x0D)))
    with pytest.raises(lmp.ProtocolError):
        lmp.PacketTableRequest.from_bytes(bytes((0xF6, 0x17, 0x00)))


def test_every_payload_fits_its_own_length_field():
    """A cross-check between two separately recovered things, and it caught a real error.

    The payload maxima come from one firmware table and the header widths from a block of leaf
    accessors; nothing makes them agree by construction. Every entry must be able to express its
    own maximum, and the first version of `length_bits` failed this on 2-DH1, whose 54 bytes do
    not fit the five-bit field it was given [protocol.md 7.3].
    """
    for entry in packets.TABLE:
        bits = packets.length_bits(entry)
        assert entry.payload < (1 << bits), (entry.name, entry.payload, bits)
        header = packets.payload_header(entry, entry.payload)
        assert packets.payload_length(entry, header) == entry.payload, entry.name


def test_the_wider_field_is_exactly_what_the_largest_packet_needs():
    """Ten bits cannot hold 2586 and twelve can, which is why the field is twelve."""
    largest = max(packets.for_table(packets.TABLE_APPLE), key=lambda e: e.payload)
    assert largest.payload == 2586
    assert largest.payload >= 1 << packets.LENGTH_BITS_SPECIFIED
    assert largest.payload < 1 << packets.LENGTH_BITS_APPLE
    assert packets.length_bits(largest) == packets.LENGTH_BITS_APPLE
    # Eleven bits would also have held it, so twelve is a choice; the mask is the evidence.
    assert packets.HEADER_MASK_APPLE == 0x7 | (1 << 15)
    assert packets.HEADER_MASK_SPECIFIED == 0x7 | (0x7 << 13)


def test_the_header_refuses_what_it_cannot_express():
    """A truncated length field is the failure this layout exists to avoid."""
    dh5 = next(e for e in packets.TABLE if e.name == "DH5")
    assert packets.payload_header(dh5, 339, llid=2, flow=1) == 2 | (1 << 2) | (339 << 3)
    with pytest.raises(ValueError):
        packets.payload_header(dh5, 2586)  # above DH5's own maximum
    with pytest.raises(ValueError):
        packets.payload_header(dh5, 10, llid=4)
    with pytest.raises(ValueError):
        packets.payload_header(dh5, 10, flow=2)
    dm1 = next(e for e in packets.TABLE if e.name == "DM1")
    assert packets.length_bits(dm1) == packets.LENGTH_BITS_ONE_SLOT
    for name in ("2-DH1", "3-DH1"):
        entry = next(e for e in packets.TABLE if e.name == name)
        assert packets.length_bits(entry) == packets.LENGTH_BITS_SPECIFIED


def test_the_power_answers_are_one_two_bit_code_five_times():
    """What 5.11 called a 1:2:3 family is one code repeated [protocol.md 5.14]."""
    for value, code in lmp.ValueAndCode.PAIRS:
        answer = lmp.ValueAndCode(value=value, code=code)
        assert len(answer.fields()) == 5
        assert answer.uniform() is not None, (value, code)
        assert set(answer.fields()) == {answer.uniform()}
    # The three answers use three different codes, which is why they looked like a ratio.
    assert sorted(lmp.ValueAndCode(value=v, code=c).uniform()
                  for v, c in lmp.ValueAndCode.PAIRS) == [1, 2, 3]
    # A mixed answer is representable and reported as mixed rather than forced into a code.
    mixed = lmp.ValueAndCode(value=0b000110, code=0b0010)
    assert mixed.uniform() is None
    assert mixed.fields() == (2, 1, 0, 2, 0)
    # The value byte's top two bits are outside the three fields, so they do not leak in.
    assert lmp.ValueAndCode(value=0xC0, code=0).fields() == (0, 0, 0, 0, 0)


def test_the_receivers_masks_fall_on_two_bit_boundaries():
    """The independent confirmation: both classes are sets of two-bit field patterns."""
    for code in lmp.ValueAndCode.FIRST_CLASS:
        assert set((code >> (2 * i)) & 3 for i in range(2)) <= {0, 3}, code
    for code in lmp.ValueAndCode.SECOND_CLASS:
        assert 2 in [(code >> (2 * i)) & 3 for i in range(2)], code
    assert lmp.ValueAndCode.FIRST_CLASS & lmp.ValueAndCode.SECOND_CLASS == frozenset()


def test_the_halfword_pair_round_trips_both_directions():
    """0x7B/0x10 and 0x7B/0x11, a request and its answer [protocol.md 5.15]."""
    for reply in (False, True):
        pdu = lmp.LinkHalfword(value=0x1234, reply=reply)
        raw = pdu.to_bytes()
        assert len(raw) == lmp.LinkHalfword.LENGTH
        assert raw[1] == (0x11 if reply else 0x10)
        assert lmp.parse(raw) == pdu
    # Little-endian, like everything else in this protocol.
    assert lmp.LinkHalfword(value=0x5B).to_bytes() == bytes((0xF6, 0x10, 0x5B, 0x00))
    with pytest.raises(lmp.ProtocolError):
        lmp.LinkHalfword(value=0x10000).to_bytes()
    with pytest.raises(lmp.ProtocolError):
        lmp.LinkHalfword.from_bytes(bytes((0xF6, 0x10, 0x01)))
    with pytest.raises(lmp.ProtocolError):
        lmp.LinkHalfword.from_bytes(bytes((0xF6, 0x0D, 0x01, 0x00)))


def test_the_counted_band_block_carries_its_own_length():
    """0x7B/0x15 and 0x7B/0x16, a count byte then that many [protocol.md 5.15]."""
    for reply in (False, True):
        pdu = lmp.BandBlock(block=bytes(range(4)), reply=reply)
        raw = pdu.to_bytes()
        assert raw[1] == (0x16 if reply else 0x15)
        assert raw[2] == 4 and len(raw) == 7
        assert lmp.parse(raw) == pdu
    assert lmp.BandBlock().to_bytes() == bytes((0xF6, 0x15, 0x00))
    assert lmp.BandBlock.FEATURE_BIT == lmp.FEATURE_BIT_BANDS
    with pytest.raises(lmp.ProtocolError):
        lmp.BandBlock(block=bytes(256)).to_bytes()
    # A count that does not match what followed is refused, not truncated silently.
    with pytest.raises(lmp.ProtocolError):
        lmp.BandBlock.from_bytes(bytes((0xF6, 0x15, 0x03, 0x01)))
    with pytest.raises(lmp.ProtocolError):
        lmp.BandBlock.from_bytes(bytes((0xF6, 0x15)))


def test_the_counted_block_is_a_tlv_list_with_the_length_in_the_high_nibble():
    """Each item is type in the low nibble, length in the high one [protocol.md 5.15]."""
    built = lmp.BandBlock.from_items([(0, b"\x5b\x00"), (1, b"\x11\x22"), (2, b"\x7f")])
    assert built.block == bytes.fromhex("205b00 211122 127f".replace(" ", ""))
    assert built.items() == ((0, b"\x5b\x00"), (1, b"\x11\x22"), (2, b"\x7f"))
    # Through the wire and back.
    assert lmp.parse(built.to_bytes()).items() == built.items()
    # An unknown type is skipped by its own declared length rather than ending the walk, which
    # is what makes the format extensible [fw acib 0x298ba to 0x298c4].
    odd = lmp.BandBlock(block=bytes.fromhex("37aabbcc") + bytes.fromhex("205b00"))
    assert odd.items() == ((7, b"\xaa\xbb\xcc"), (0, b"\x5b\x00"))
    assert 7 not in lmp.BandBlock.ITEM_FIELDS
    with pytest.raises(lmp.ProtocolError):
        lmp.BandBlock.from_items([(0x10, b"")])
    with pytest.raises(lmp.ProtocolError):
        lmp.BandBlock.from_items([(0, bytes(16))])


def test_the_firmware_reply_block_and_the_halfword_pair_agree():
    """One value, two messages, one field: the cross-check behind both readings.

    The reply this firmware builds opens with a type 0 item carrying 0x005B, and type 0 writes
    the link's +0x62; the halfword pair's own answer carries 0x5B to the same +0x62. Two
    functions building two messages that agree on a value and a destination is not what a
    misreading of either produces [protocol.md 5.15].
    """
    opening = lmp.BandBlock(block=lmp.BandBlock.REPLY_OPENS_WITH).items()
    assert len(opening) == 1
    kind, value = opening[0]
    assert kind == 0
    assert int.from_bytes(value, "little") == lmp.LinkHalfword.SEEN_IN_REPLY
    assert lmp.BandBlock.ITEM_FIELDS[kind] == 0x62
