"""The host interface, against the bytes that were measured and the layouts that were read."""
import pytest

from openulla import aap
from openulla.errors import ProtocolError

# The exact frames a Linux host exchanged with real earbuds, from docs/protocol.md 2.2.
MEASURED_OPEN_REQUEST = bytes.fromhex("00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00")
MEASURED_OPEN_REPLY = bytes.fromhex("01 00 04 00 00 00 01 00 03 00 00 00 00 00 00 00 00 00")


def test_open_request_is_the_measured_frame():
    assert aap.open_request(aap.SERVICE_ULLA, 1, 2) == MEASURED_OPEN_REQUEST
    assert len(MEASURED_OPEN_REQUEST) == aap.OPEN_REQUEST_LENGTH


def test_open_reply_parses_the_measured_frame():
    frame = aap.parse_frame(MEASURED_OPEN_REPLY)
    assert (frame.ftype, frame.service) == (aap.FrameType.OPEN_REPLY, aap.SERVICE_ULLA)
    reply = aap.OpenReply.from_bytes(frame.body)
    assert reply.ok and (reply.major, reply.minor) == (1, 3)


def test_our_open_reply_round_trips_to_the_measured_bytes():
    assert aap.open_reply(1, 3) == MEASURED_OPEN_REPLY


def test_open_reply_body_length_is_fourteen():
    with pytest.raises(ProtocolError):
        aap.OpenReply.from_bytes(bytes(13))


def test_commands_carry_the_body_lengths_the_firmware_reads():
    assert aap.start_session(2) == bytes.fromhex("04 00 04 00 36 00 02")
    assert aap.start_stream() == bytes.fromhex("04 00 04 00 38 00")
    assert aap.stop_stream() == bytes.fromhex("04 00 04 00 3a 00")
    assert len(aap.parse_payload(aap.parse_frame(aap.stop_session(7)).body)[1]) == 4
    assert len(aap.parse_payload(aap.parse_frame(aap.abort_response(7)).body)[1]) == 4
    assert len(aap.parse_payload(aap.parse_frame(aap.configure_statistics(1, 2, 3)).body)[1]) == 9


def test_status_reply_is_six_bytes_of_payload():
    body = aap.parse_frame(aap.status_reply(0x1776)).body
    assert len(body) == 6
    assert aap.parse_status(body) == 0x1776


def test_wrapper_class_width_follows_the_version_pair():
    assert aap.class_width(0x00010002) == 4
    assert aap.class_width(0x00010001) == 2
    assert aap.class_width(0x00010000) == 2


def test_wrapper_round_trips_at_both_widths():
    for width in (2, 4):
        frame = aap.wrapper(0x1234, b"\xaa\xbb", width)
        wrapped = aap.parse_wrapper(aap.parse_frame(frame).body, width)
        assert (wrapped.cls_, wrapped.body) == (0x1234, b"\xaa\xbb")


def test_subscribe_names_the_ulla_report_class():
    frame = aap.subscribe_reports()
    opcode, rest = aap.parse_payload(aap.parse_frame(frame).body)
    assert opcode == aap.Opcode.WRAPPER
    assert int.from_bytes(rest[:4], "little") == 0x00200000
    assert aap.parse_wrapper(aap.parse_frame(frame).body).body == bytes(4)


def test_a_class_too_wide_for_its_field_is_refused():
    with pytest.raises(ProtocolError):
        aap.wrapper(0x00200000, b"", width=2)


def test_report_body_starts_with_its_type_and_two_manager_bytes():
    report = aap.parse_report(bytes((7, 1, 0)) + b"rest")
    assert (report.report_type, report.manager_fields, report.data) == (7, (1, 0), b"rest")


def test_the_siblings_copy_of_a_report_carries_one_byte_more():
    report = aap.parse_report(bytes((4, 7, 1, 0)) + b"rest", sibling=True)
    assert (report.report_type, report.manager_fields, report.data) == (7, (1, 0), b"rest")
    with pytest.raises(ProtocolError):
        aap.parse_report(bytes((5, 7, 1, 0)), sibling=True)
    with pytest.raises(ProtocolError):
        aap.parse_report(bytes((4, 7, 1)), sibling=True)


def test_each_report_type_has_one_notification_id_and_one_length():
    assert aap.REPORTS == {0: (0x0A08, 56), 1: (0xE910, 185), 2: (0xE911, 61)}
    # All three are described by the firmware and unreachable in it, each for its own reason,
    # and 2.6 shows it from both ends: nothing fills type 0, nothing asks for type 1.
    assert aap.REPORTS_A_HOST_SEES == ()
    assert set(aap.REPORTS_A_HOST_SEES) < set(aap.REPORTS)


def test_wrapper_length_must_match_its_payload():
    bad = aap.data_frame(aap.Opcode.WRAPPER, (0).to_bytes(4, "little") + b"\x08\x00" + b"\x01")
    with pytest.raises(ProtocolError):
        aap.parse_wrapper(aap.parse_frame(bad).body)


def test_the_three_report_types_carry_their_own_length_and_name_their_notification():
    for report_type, (notification, length) in aap.REPORTS.items():
        report = aap.parse_report(bytes((report_type, 1, 0)) + bytes(length))
        assert report.report_type == report_type
        assert report.notification == notification
        assert len(report.data) == length


def test_a_known_report_type_with_the_wrong_length_is_refused():
    """The firmware's own dispatcher logs this mismatch, so a decoder may not shrug at it."""
    with pytest.raises(ProtocolError):
        aap.parse_report(bytes((1, 1, 0)) + bytes(184))
    sibling = bytes((aap.Report.SIBLING_MARKER, 2, 1, 0))
    aap.parse_report(sibling + bytes(61), sibling=True)
    with pytest.raises(ProtocolError):
        aap.parse_report(sibling + bytes(62), sibling=True)


def test_a_report_type_nobody_has_read_is_passed_through():
    report = aap.parse_report(bytes((7, 1, 0)) + b"whatever")
    assert (report.report_type, report.notification, report.data) == (7, None, b"whatever")
