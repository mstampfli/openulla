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


def test_the_payload_ratios_say_a_factor_of_two_and_the_one_slot_pair_does_not_fit():
    """What the payloads actually support, including the pair that does not.

    An earlier version of this test ran the ratio check over `(3, 5)` only, which excluded the
    one pair that fails it, and the documentation it was supposed to support said "within each
    slot length" on that basis. All three are covered here, and the one-slot pair is asserted to
    be the outlier it is rather than left out [protocol.md 5.13].
    """
    def pair(slots):
        low = next(e for e in packets.TABLE if e.slots == slots and "4mbit" in e.name)
        high = next(e for e in packets.TABLE if e.slots == slots and "8mbit" in e.name)
        return low, high

    ratios = {slots: pair(slots)[1].payload / pair(slots)[0].payload for slots in (1, 3, 5)}
    # The three- and five-slot pairs sit just above two.
    assert 2.00 <= ratios[3] < 2.02, ratios
    assert 2.00 <= ratios[5] < 2.02, ratios
    # The one-slot pair does not, and the reason is not established.
    assert ratios[1] > 2.15, ratios
    assert ratios[1] == 242 / 112

    def window(name, rate):
        entry = next(e for e in packets.TABLE if e.name == name)
        return packets.payload_window_us(entry, rate)

    # Dividing by a rate is scale-invariant, so it fixes the ratio of the two rates and not
    # their size: whatever pair is tried, the relative spread is identical, which is the whole
    # point. That is why `packets.APPLE_RATES` cites Apple's filing for the absolute values
    # rather than this arithmetic.
    #
    # Every slot count is covered here, and the threshold is one the three-slot pair actually
    # meets. The first version of this loop computed the five-slot spread alone under a 0.005
    # threshold, which was the single value admitting the five-slot pair (0.0039) while
    # excluding the three-slot pair (0.0071) the prose cites in the same breath - the same
    # defect as the ratio check it was written to fix.
    spreads = {}
    for low_rate, high_rate in ((4, 8), (4.5, 9), (3, 6)):
        for slots in (1, 3, 5):
            low, high = pair(slots)
            base = packets.payload_window_us(low, low_rate)
            rel = abs(base - packets.payload_window_us(high, high_rate)) / base
            spreads.setdefault(slots, set()).add(round(rel, 9))
    assert all(len(v) == 1 for v in spreads.values()), f"not scale-invariant: {spreads}"
    assert spreads[3].pop() < 0.008 and spreads[5].pop() < 0.008
    # And the one-slot pair is the outlier here too, by a factor of ten.
    assert spreads[1].pop() > 0.08
    assert packets.APPLE_RATES == (4, 8)
    with pytest.raises(ValueError):
        packets.payload_window_us(packets.TABLE[0], 0)


def test_the_chooser_reproduces_what_a_standard_host_can_ask_for():
    """Given only the sixteen-bit field, the best it can pick is 3-DH5 [measurements.md 9].

    Every call names its table, because the legality constant is applied before the search and
    changes the answer rather than widening it. The same 0xFFFF request resolves to a different
    packet on each of the three tables, and on table 2 it resolves to DM1 - the one entry all
    three share - because a standard host cannot reach bits 16 to 21 at all.
    """
    assert packets.choose(0xFFFF, packets.TABLE_EDR).name == "3-DH5"
    assert packets.choose(0xFFFF, packets.TABLE_BASIC).name == "DH5"
    assert packets.choose(0xFFFF, packets.TABLE_APPLE).name == "DM1"
    assert packets.choose(0xFFFFFFFF, packets.TABLE_APPLE).name == "apple_8mbit_5slot"
    # And the mask bits of the other tables' entries cannot smuggle one in.
    assert packets.choose(0xFFFFFFFF, packets.TABLE_EDR).name == "3-DH5"
    assert packets.choose(1 << 3, packets.TABLE_APPLE).name == "DM1"
    assert packets.choose(0, packets.TABLE_APPLE) is None
    with pytest.raises(ValueError):
        packets.choose(0xFFFF, 3)
    # The three constants are the firmware's statement of the whole tables column.
    for table, legal in packets.TABLE_LEGAL.items():
        for entry in packets.TABLE:
            assert bool(legal & (1 << entry.mask_bit)) == (table in entry.tables), entry.name
    # Tables 0 and 1 clear Apple's six bits; table 2 is the only one that allows them.
    for table in (packets.TABLE_BASIC, packets.TABLE_EDR):
        assert packets.TABLE_LEGAL[table] & 0x3F0000 == 0
    assert packets.TABLE_LEGAL[packets.TABLE_APPLE] & 0x3F0000 == 0x3F0000
    assert packets.NO_MATCH_PAYLOAD == packets.TABLE[0].payload == 17
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
    # Twelve is the narrowest field that holds it: eleven bits reach only 2047.
    assert largest.payload > (1 << 11) - 1
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


def test_the_permission_byte_partitions_the_six_into_the_same_two_groups():
    """A third confirmation of the **partition**, which is not a confirmation of the rates.

    The packet-type mask filter keeps one set of Apple's mask bits when bit 4 of the permission
    byte is set and the other only when bits 3 and 4 are both set [fw acib 0x13778 to 0x13786].
    Those two sets are exactly the two groups of three that the TYPE codes and the rate-class
    array give, so the partition has three independent sources.

    What it does **not** do is say which group is the faster, and an earlier version of this
    test claimed it did. The same filter's other branch gates the specification's own two EDR
    rates with the same two bits, and there "kept by bit 4 alone" is the **3** Mb/s set - the
    faster one - while in Apple's branch that condition keeps the group with the *smaller*
    payloads. Transferring the shape gives the opposite answer, so it cannot be the argument.
    The orientation comes from the payloads at a fixed slot count, and the absolute 4 and 8
    from Apple's filings [see `packets.APPLE_RATES`]. The inversion is asserted below so the
    claim cannot quietly come back.
    """
    def names(value):
        pdu = lmp.PacketPermission(permission=value)
        return {p.name for p in pdu.allows() if p.mask_bit in packets.APPLE_MASK_BITS}

    four = {e.name for e in packets.for_table(packets.TABLE_APPLE) if "4mbit" in e.name}
    eight = {e.name for e in packets.for_table(packets.TABLE_APPLE) if "8mbit" in e.name}
    assert len(four) == 3 and len(eight) == 3
    # Bit 4 alone: exactly the slower set.
    assert names(0x10) == four
    # Bits 3 and 4: both sets.
    assert names(0x18) == four | eight
    # Bit 3 alone, or neither: none of the six.
    assert names(0x08) == set()

    # Anchored on the firmware's two immediates, so that exchanging them in the image turns
    # this red. Without it the assertions above hold on `mask_bit % 2` alone.
    def bits(mask):
        return {e.name for e in packets.TABLE if (1 << e.mask_bit) & mask}

    assert bits(packets.PERMISSION_MASK_BIT_FOUR) == four
    assert bits(packets.PERMISSION_MASK_BOTH_BITS) == eight
    assert (packets.PERMISSION_MASK_BIT_FOUR | packets.PERMISSION_MASK_BOTH_BITS) == sum(
        1 << b for b in packets.APPLE_MASK_BITS
    ), "the two gates must partition exactly the six, with no bit in both or in neither"
    assert not packets.PERMISSION_MASK_BIT_FOUR & packets.PERMISSION_MASK_BOTH_BITS

    # The inversion, which is why the gates cannot orient the rates: in the standard branch the
    # bit-4-alone condition keeps the 3 Mb/s set, the faster of the specification's two.
    assert bits(packets.STANDARD_MASK_BIT_FOUR) == {"3-DH1", "3-DH3", "3-DH5"}
    assert bits(packets.STANDARD_MASK_BOTH_BITS) == {"2-DH1", "2-DH3", "2-DH5"}
    standard_faster = bits(packets.STANDARD_MASK_BIT_FOUR)
    apple_same_gate = bits(packets.PERMISSION_MASK_BIT_FOUR)
    assert max(e.payload for e in packets.TABLE if e.name in standard_faster) > max(
        e.payload for e in packets.TABLE if e.name in bits(packets.STANDARD_MASK_BOTH_BITS)
    ), "the bit-4 set is the larger-payload one among the specification's EDR pairs"
    assert max(e.payload for e in packets.TABLE if e.name in apple_same_gate) < max(
        e.payload for e in packets.TABLE if e.name in bits(packets.PERMISSION_MASK_BOTH_BITS)
    ), "and the smaller-payload one among Apple's, which is the inversion"
    assert names(0x00) == set()
    # The even mask bits are the slower set and the odd ones the faster, which is what makes
    # the filter's two tests line up with the two rates at all.
    assert {e.mask_bit % 2 for e in packets.for_table(packets.TABLE_APPLE)
            if e.name in four} == {0}
    assert {e.mask_bit % 2 for e in packets.for_table(packets.TABLE_APPLE)
            if e.name in eight} == {1}


def test_the_permission_message_round_trips_and_reads_signed():
    from openulla import lmp as module

    assert module.PacketPermission(permission=0x18).to_bytes() == bytes((0xF6, 0x0C, 0x18))
    assert module.parse(bytes((0xF6, 0x0C, 0xFF))) == module.PacketPermission(permission=0xFF)
    assert module.PacketPermission(permission=0xFF).signed() == -1
    assert module.PacketPermission(permission=0x7F).signed() == 127
    with pytest.raises(module.ProtocolError):
        module.PacketPermission(permission=0x100).to_bytes()
    with pytest.raises(module.ProtocolError):
        module.PacketPermission.from_bytes(bytes((0xF6, 0x0C)))
    with pytest.raises(module.ProtocolError):
        module.PacketPermission.from_bytes(bytes((0xF6, 0x0D, 0x00)))
