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
