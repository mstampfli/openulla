"""The vendor commands, the refusals the firmware makes, and the mode-change event."""
import pytest

from openulla import hci, lmp
from openulla.codes import Mode, RejectReason, Result
from openulla.errors import ProtocolError


def test_the_opcodes_are_the_vendor_group_and_the_command_ids_the_firmware_uses():
    assert hci.vendor_opcode(0x250) == hci.OPCODE_ULLA_SETUP == 0xFE50
    assert hci.vendor_opcode(0x379) == hci.OPCODE_ALLOWED_BANDS == 0xFF79
    assert hci.vendor_opcode(0x37A) == hci.OPCODE_ALLOWED_BANDS_AGAIN == 0xFF7A
    assert hci.vendor_opcode(0x392) == hci.OPCODE_COUNTRY_CODES == 0xFF92
    assert hci.vendor_opcode(0x3B3) == hci.OPCODE_TEST_BAND_RANGE == 0xFFB3
    assert hci.OPCODE_ULLA_SETUP >> 10 == hci.OGF_VENDOR


def test_the_setup_command_has_seven_parameter_bytes_in_the_documented_order():
    setup = hci.UllaSetup(handle=0x000B, extra_flags=0x02, config_word=0x0104, flags=0x0B)
    assert setup.to_bytes() == bytes.fromhex("0b 00 02 04 01 0b 00")
    assert setup.command() == bytes.fromhex("50 fe 07 0b 00 02 04 01 0b 00")
    assert hci.UllaSetup.from_bytes(setup.to_bytes()) == setup
    with pytest.raises(ProtocolError):
        hci.UllaSetup.from_bytes(bytes(6))


def test_the_h4_byte_is_added_only_when_asked():
    assert hci.h4(hci.band_gate(True)) == bytes.fromhex("01 77 ff 01 01")


def test_the_mask_commands_always_travel_as_a_pair():
    first, second = hci.allowed_bands(0xF0)
    assert first == bytes.fromhex("79 ff 01 f0")
    assert second == bytes.fromhex("7a ff 01 f0")
    assert first[3:] == second[3:]


def test_the_masks_and_indexes_the_handlers_refuse():
    with pytest.raises(ProtocolError):
        hci.allowed_bands(0)
    with pytest.raises(ProtocolError):
        hci.allowed_bands(0b1000)
    with pytest.raises(ProtocolError):
        hci.set_link_band(0x0B, 9)
    hci.set_link_band(0x0B, 8)  # eight means none
    with pytest.raises(ProtocolError):
        hci.country_code(8)
    with pytest.raises(ProtocolError):
        hci.country_codes([0, 8])
    hci.country_codes([0, 0xFF])
    with pytest.raises(ProtocolError):
        hci.test_band_and_range(3, 0, 1)
    with pytest.raises(ProtocolError):
        hci.test_band_and_range(1, 13, 6)  # band 1 has five channels
    assert hci.test_band_and_range(1, 13, 5) == bytes.fromhex("b3 ff 03 01 0d 05")


def test_band_ranges_are_checked_against_the_band_table():
    assert hci.band_ranges([(5, 29, 6)]) == bytes.fromhex("8e ff 04 01 05 1d 06")
    with pytest.raises(ProtocolError):
        hci.band_ranges([(5, 29, 7)])
    with pytest.raises(ProtocolError):
        hci.band_ranges([])


def test_the_mode_change_event_is_seven_parameters_behind_the_event_id():
    event = hci.ModeChangeEvent(handle=0x000B, mode=Mode.ULLA_ON, result=Result.SUCCESS,
                                reason=RejectReason.NOT_APPLICABLE)
    raw = event.to_bytes()
    assert raw == bytes.fromhex("ff 07 00 0a 0b 00 01 00 ff")
    assert raw[1] == hci.ModeChangeEvent.PARAM_LENGTH == 7
    assert int.from_bytes(raw[2:4], "little") == hci.EVENT_ID_MODE_CHANGE
    assert hci.ModeChangeEvent.from_bytes(raw) == event
    assert event.describe() == "handle 0x000b ULLA_ON SUCCESS NOT_APPLICABLE"


def test_a_rejection_keeps_the_peers_reason_byte():
    event = hci.ModeChangeEvent(1, Mode.BT_ACL_ACTIVE, Result.SETUP_REJECTED_BY_PEER, 5)
    assert "ILLEGAL_NUM_CONNECTIONS" in event.describe()
    assert "INVALID_REASON" in hci.ModeChangeEvent(1, 0, 1, 2).describe()


def test_an_event_whose_length_disagrees_with_itself_is_refused():
    with pytest.raises(ProtocolError):
        hci.ModeChangeEvent.from_bytes(bytes.fromhex("ff 06 00 0a 0b 00 01 00 ff"))
    with pytest.raises(ProtocolError):
        hci.ModeChangeEvent.from_bytes(bytes.fromhex("ff 05 09 0a 0b 00 01"))
    with pytest.raises(ProtocolError):
        hci.ModeChangeEvent.from_bytes(bytes.fromhex("3e 07 00 0a 0b 00 01 00 ff"))


def test_the_setup_parameters_the_earbuds_own_manager_uses():
    """Constants, not inventions: protocol.md 6.0."""
    ulla = hci.manager_setup(0x000B, 1)
    assert ulla.to_bytes() == bytes.fromhex("0b 00 01 96 00 04 01")
    assert ulla.command() == bytes.fromhex("50 fe 07 0b 00 01 96 00 04 01")
    for field in (0, 2, 0xFF):
        other = hci.manager_setup(0x000B, field)
        assert other.flags == hci.MANAGER_FLAGS_OTHERWISE
        assert other.mode == field
        assert other.config_word == hci.MANAGER_CONFIG_WORD == 0x96
        assert other.extra_flags == hci.MANAGER_EXTRA_FLAGS == 1


def pdu_from(setup):
    """The setup PDU a controller would emit for those command parameters."""
    from openulla import lmp
    from openulla.controller import Initiator

    return lmp.SetupRequest.from_bytes(Initiator().setup_request(setup))


def test_the_flag_bits_that_reach_the_pdu_follow_from_those_parameters():
    for_mode_one = pdu_from(hci.manager_setup(0x000B, 1))
    # The manager's flag byte for mode 1 is 4: only bit 2, which reaches the context and
    # never the air, so both of the PDU's flag bytes are zero.
    assert (for_mode_one.flags, for_mode_one.extra_flags) == (0, 0)
    assert for_mode_one.config_word == 0x0096
    # For any other mode it is 0xF: bits 0 and 1 make the first byte 3, bit 3 the second 1.
    for_mode_zero = pdu_from(hci.manager_setup(0x000B, 0))
    assert (for_mode_zero.flags, for_mode_zero.extra_flags) == (0b11, 1)


def test_the_second_ulla_command_is_the_handle_a_zero_and_the_mode_field():
    assert hci.ulla_second_command(0x000B, 2) == bytes.fromhex("51 fe 04 0b 00 00 02")
    with pytest.raises(ProtocolError):
        hci.ulla_second_command(0x000B, 0x100)


def test_the_refusal_the_procedure_path_would_answer():
    """Parameter 5 is a value, not a bitmask, once the mode is non-zero. [protocol.md 6]"""
    assert hci.manager_setup(0x000B, 0).refusal() is None
    assert hci.manager_setup(0x000B, 1).refusal() is None
    assert hci.manager_setup(0x000B, 2).refusal() == 0x12  # the firmware's own pairing
    assert hci.UllaSetup(1, 0, 0, 4, mode=2).refusal() is None
    assert hci.UllaSetup(1, 0, 0, 5, mode=2).refusal() == 0x12
    assert hci.UllaSetup(1, 0, 0, 0xFF, mode=0).refusal() is None  # a bitmask on that path


def test_the_two_commands_that_set_the_controllers_own_feature_bits():
    """One bit each way: 0xFF77 for bit 5, 0xFDF3 for the group with bit 6. [protocol.md 5.8]"""
    assert hci.band_gate(True) == bytes.fromhex("77 ff 01 01")
    assert hci.band_gate(False) == bytes.fromhex("77 ff 01 00")
    assert hci.feature_group(True) == bytes.fromhex("f3 fd 01 01")
    assert hci.feature_group(False) == bytes.fromhex("f3 fd 01 00")
    assert hci.OPCODE_FEATURE_GROUP >> 10 == hci.OGF_VENDOR
    # The two groups of that byte are complementary, which is how the firmware splits it.
    assert 0xA9 | 0x56 == 0xFF and 0xA9 & 0x56 == 0
    assert (1 << lmp.FEATURE_BIT_BANDS) & 0xA9  # bit 5 is in the group 0xFDF3 leaves alone
    assert (1 << lmp.FEATURE_BIT_MODE_PROCEDURE) & 0x56  # bit 6 is in the group it adds
