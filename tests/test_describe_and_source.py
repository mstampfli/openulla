"""What the decoders say about every message, and what the source side builds.

These are the pure functions a reader meets first: the wording of each decode and the bytes
of each command. Coverage of openulla/describe.py, source.py and transport.py was measured and
these are the paths it showed untested.
"""
import pytest

from openulla import aap, bands, describe, hci, lmp, sibling, source, transport
from openulla.codes import HciError, Mode, RejectReason, Result
from openulla.errors import ProtocolError
from openulla.sim import DEFAULT_PEER
from openulla.source import ControllerCommands, Source, describe_status


def joined(lines):
    return "\n".join(lines)


def test_every_aap_frame_type_is_named():
    assert "open" in joined(describe.describe_aap(aap.open_request()))
    assert "open_reply" in joined(describe.describe_aap(aap.open_reply(1, 3)))
    assert "control_2" in joined(describe.describe_aap(aap.frame(2, 4, b"\x00")))
    said = joined(describe.describe_aap(aap.stop_session(7)))
    assert "STOP_SESSION" in said


def test_the_status_and_the_subscription_are_spelled_out():
    assert "SIBLING_TRANSPORT_NOT_READY" in joined(describe.describe_aap(aap.status_reply(0x1776)))
    assert "the ULLA report streams" in joined(describe.describe_aap(aap.subscribe_reports()))
    other = aap.wrapper(0x00300000, b"\x01\x02")
    assert "class 0x00300000" in joined(describe.describe_aap(other))


def test_every_link_manager_pdu_this_package_builds_can_be_described():
    configuration = describe.describe_lmp(lmp.Configuration(7, 9).to_bytes())
    assert "the ULLA configuration" in joined(configuration)
    assert "accepted, for opcode 0x73" in joined(
        describe.describe_lmp(lmp.Accepted(lmp.OPCODE_ULLA_SETUP).to_bytes())
    )
    refused = describe.describe_lmp(
        lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, RejectReason.PRIMARY_SNOOP_DISCONNECTED).to_bytes()
    )
    assert "PRIMARY_SNOOP_DISCONNECTED" in joined(refused)
    empty = describe.describe_lmp(lmp.BandRanges().to_bytes())
    assert "0 triples" in joined(empty)
    trailing = describe.describe_lmp(bytes.fromhex("f6 1c 05 1d 06 07"))
    assert "trailing bytes" in joined(trailing)


def test_an_extended_opcode_this_build_refuses_is_called_refused():
    said = joined(describe.describe_lmp(bytes((0x7B << 1, 0x24, 0x01, 0x02))))
    assert "refused" in said  # the earbud would not dispatch it
    assert "not one this package decodes" in said  # and we do not pretend to know its body
    assert "01 02" in said  # but the bytes are there to read


def test_a_message_only_the_peer_implements_still_decodes_its_header():
    """0x7B/0x28 is sent by the earbuds and refused by them; a capture may hold it."""
    said = joined(describe.describe_lmp(bytes((0x7B << 1, 0x28)) + bytes(12)))
    assert "extended opcode 0x28" in said and "refused" in said


def test_the_band_commands_are_described_with_their_bands():
    first, _second = hci.allowed_bands(0xE6)
    said = joined(describe.describe_hci_command(first))
    assert "5150-5250" in said and "6285-6425" in said
    gate_on = joined(describe.describe_hci_command(hci.band_gate(True)))
    gate_off = joined(describe.describe_hci_command(hci.band_gate(False)))
    assert "switched on" in gate_on and "switched off" in gate_off
    link = joined(describe.describe_hci_command(hci.set_link_band(0x0B, 5)))
    assert "is set to band 5" in link
    none = joined(describe.describe_hci_command(hci.set_link_band(0x0B, bands.NO_BAND)))
    assert "is set to none" in none
    codes = joined(describe.describe_hci_command(hci.country_codes([0, 0xFF, 3])))
    assert "unavailable" in codes


def test_the_second_ulla_command_and_a_mask_with_the_empty_band():
    second = joined(describe.describe_hci_command(hci.ulla_second_command(0x0B, 2)))
    assert "the second ULLA command" in second and "mode 2" in second
    # A mask naming band 3 is refused by the controller, and the decoder says why.
    mask = joined(describe.describe_bands(0b1000))
    assert "every path refuses" in mask


def test_the_events_a_host_sees_are_described():
    event = hci.ModeChangeEvent(0x0B, Mode.ULLA_LITE_ON, Result.LSTO_ERROR, 0xAA)
    said = joined(describe.describe_hci_event(event.to_bytes()))
    assert "ULLA_LITE_ON" in said and "LSTO_ERROR" in said
    second = bytes.fromhex("ff 05 09 0a 0b 00 07")
    assert "the second ULLA event" in joined(describe.describe_hci_event(second))
    complete = bytes.fromhex("0e 04 01 50 fe 0c")
    said = joined(describe.describe_hci_event(complete))
    assert "command complete for 0xfe50" in said
    assert HciError.COMMAND_DISALLOWED.name in said
    ok = bytes.fromhex("0e 04 01 50 fe 00")
    assert "success" in joined(describe.describe_hci_event(ok))


def test_a_command_or_event_whose_length_lies_is_refused():
    with pytest.raises(ProtocolError):
        describe.describe_hci_command(bytes.fromhex("50 fe 07 00"))
    with pytest.raises(ProtocolError):
        describe.describe_hci_event(bytes.fromhex("ff 07 00"))
    with pytest.raises(ProtocolError):
        describe.describe_hci_command(b"\x01")


def test_the_source_sends_each_command_and_reads_the_status_back():
    from openulla.sim import SinkTransport
    from openulla.sink import Sink

    channel = SinkTransport(Sink(), DEFAULT_PEER)
    source = Source(channel)
    source.open_service(timeout=None)
    assert source.version_pair == (1 << 16) | 3
    source.start_session(1)
    source.subscribe_reports()
    source.start_stream()
    source.stop_stream()
    source.stop_session(3)
    sent = [aap.parse_payload(aap.parse_frame(frame).body)[0] for frame in channel.sent[1:]]
    assert sent == [
        aap.Opcode.START_SESSION,
        aap.Opcode.WRAPPER,
        aap.Opcode.START_STREAM,
        aap.Opcode.STOP_STREAM,
        aap.Opcode.STOP_SESSION,
    ]
    source.close()


def test_the_source_refuses_an_answer_that_is_not_an_open_reply():
    from openulla.sim import SinkTransport
    from openulla.sink import Sink

    channel = SinkTransport(Sink(), DEFAULT_PEER)
    channel.deliver(aap.status_reply(0))
    with pytest.raises(ProtocolError):
        Source(channel).open_service(timeout=None)
    refusing = SinkTransport(Sink(), DEFAULT_PEER)
    refusing.deliver(aap.frame(aap.FrameType.OPEN_REPLY, aap.SERVICE_ULLA,
                               aap.OpenReply(0x68, 1, 3).to_bytes()))
    with pytest.raises(ProtocolError):
        Source(refusing).open_service(timeout=None)


def test_the_controller_commands_a_source_would_need():
    commands = ControllerCommands(0x000B)
    assert commands.enable_band_commands() == bytes.fromhex("77 ff 01 01")
    assert commands.read_band() == bytes.fromhex("78 ff 02 0b 00")
    assert commands.choose_band(5) == bytes.fromhex("7c ff 03 0b 00 05")
    assert commands.announce_bands(0xE6)[0] == bytes.fromhex("79 ff 01 e6")
    assert commands.start_transition(config_word=0x0100, flags=3)[0:3] == bytes.fromhex("50 fe 07")
    assert ControllerCommands.mask_for((1, 2, 5, 6, 7)) == 0xE6
    with pytest.raises(ProtocolError):
        ControllerCommands.mask_for((3,))  # the empty band is refused
    with pytest.raises(ProtocolError):
        ControllerCommands.mask_for(())  # and so is an empty mask


def test_a_status_is_named_or_shown_as_a_number():
    assert describe_status(0) == "OK"
    assert describe_status(0x1776) == "SIBLING_TRANSPORT_NOT_READY"
    assert describe_status(0x1234) == "0x1234"


def test_the_queue_transport_is_a_pair_that_carries_frames_both_ways():
    host, bud = transport.QueueTransport.pair()
    host.send(b"\x01\x02")
    assert bud.pending() == 1
    assert bud.recv() == b"\x01\x02"
    bud.send(b"\x03")
    assert host.recv(timeout=0.1) == b"\x03"
    with pytest.raises(TimeoutError):
        host.recv()
    host.close()
    with pytest.raises(ProtocolError):
        host.send(b"\x04")


def test_the_l2cap_transport_says_so_when_the_platform_cannot_do_it(monkeypatch):
    monkeypatch.delattr("socket.AF_BLUETOOTH", raising=False)
    with pytest.raises(ProtocolError):
        transport.L2capTransport.connect("11:22:33:44:55:66")


def test_the_sibling_messages_with_bodies_round_trip():
    stats = sibling.statistics(b"\x07\x01\x00data")
    parsed = sibling.parse_frame(stats)
    assert parsed.client == sibling.CLIENT_SESSION_MANAGER
    assert parsed.message == sibling.Message.STATISTICS
    assert aap.parse_report(parsed.body).report_type == 7


def test_the_source_can_set_both_halves_of_the_gate():
    """0xFF77 for the band family, 0xFDF3 for the mode procedure. [protocol.md 5.8]"""
    commands = source.ControllerCommands(handle=0x000B)
    assert commands.enable_band_commands() == bytes.fromhex("77 ff 01 01")
    assert commands.enable_mode_procedure() == bytes.fromhex("f3 fd 01 01")


def test_the_sibling_decode_names_the_message_and_flags_a_body_that_should_be_empty():
    """The layer the vectors added, including the two branches a happy frame does not take."""
    request = sibling.start_session_request(bytes.fromhex("112233445566"), 1)
    lines = joined(describe.describe_sibling(request))
    assert "START_SESSION_REQ" in lines and "11:22:33:44:55:66" in lines and "mode 1" in lines

    # A message the firmware sends empty, with bytes after it: the decode says so rather than
    # inventing a field for them [protocol.md section 4].
    padded = sibling.frame(sibling.Message.SESSION_STOPPED, b"\x01\x02")
    assert "these bytes are extra" in joined(describe.describe_sibling(padded))

    # And a client that is not the session manager is named as the number it is.
    other = sibling.frame(sibling.Message.START_STREAM_REQ, client=9)
    decoded = joined(describe.describe_sibling(other))
    assert "client 9" in decoded and "session manager" not in decoded


def test_a_number_with_no_name_decodes_to_its_hex():
    """describe.named falls back rather than raising, which every decode relies on."""
    assert describe.named(0x5A, Mode) == "0x5a"
    assert describe.named(int(Mode.ULLA_ON), Mode).startswith("ULLA_ON")


def test_the_configuration_decode_warns_where_0x75_would_drop_the_halfword():
    over = lmp.Configuration(0, lmp.Configuration.MAX_VALUE16_FOR_75 + 1,
                             opcode=lmp.OPCODE_ULLA_CONFIG_ALT)
    assert "drops a halfword above" in joined(describe.describe_lmp(over.to_bytes()))


def test_the_setup_decode_says_when_the_controller_would_refuse_it():
    # On the procedure path the last parameter is a value 0 to 4, so five is refused with 0x12.
    refused = hci.UllaSetup(handle=0x000B, extra_flags=0, config_word=0, flags=5, mode=9)
    lines = joined(describe.describe_hci_command(hci.command(hci.OPCODE_ULLA_SETUP,
                                                             refused.to_bytes())))
    assert "would refuse this" in lines


def test_a_two_byte_class_wrapper_still_decodes():
    """The narrow class field of an older version pair [protocol.md 2.6]."""
    narrow = aap.data_frame(aap.Opcode.WRAPPER, aap.wrapper(0, b"", width=2)[6:])
    assert "wrapper" in joined(describe.describe_aap(narrow))
