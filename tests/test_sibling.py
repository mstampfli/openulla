"""The earbud-to-earbud messages, including the address order that proves itself."""
import pytest

from openulla import sibling
from openulla.errors import ProtocolError

STACK_ORDER = bytes.fromhex("112233445566")  # as HCI and BlueZ hold an address


def test_a_frame_is_the_client_id_then_the_message_id():
    assert sibling.frame(sibling.Message.STOP_SESSION_REQ) == bytes((5, 3))
    parsed = sibling.parse_frame(bytes((5, 12)) + b"report")
    assert (parsed.client, parsed.message, parsed.body) == (5, 12, b"report")


def test_the_start_request_reverses_the_address_and_reverses_it_back():
    raw = sibling.start_session_request(STACK_ORDER, 1)
    assert raw == bytes.fromhex("05 01 66 55 44 33 22 11 01")
    parsed = sibling.parse_start_session_request(raw)
    assert parsed.address == STACK_ORDER and parsed.mode == 1


def test_an_address_that_is_not_six_bytes_is_refused():
    with pytest.raises(ProtocolError):
        sibling.start_session_request(b"\x01\x02", 0)


def test_the_message_ids_are_the_firmwares_own_numbers():
    assert sibling.Message.START_SESSION_REQ == 1
    assert sibling.Message.STOP_STREAM_RSP == 8
    assert sibling.Message.STATISTICS == 12
    assert sibling.Message.CONFIGURE_STATISTICS == 13


def test_the_messages_with_bodies_are_not_in_the_empty_set():
    assert sibling.Message.START_SESSION_REQ not in sibling.EMPTY_BODY
    assert sibling.Message.STATISTICS not in sibling.EMPTY_BODY
    assert sibling.Message.CONFIGURE_STATISTICS not in sibling.EMPTY_BODY
    with pytest.raises(ProtocolError):
        sibling.empty(sibling.Message.STATISTICS)


def test_configure_statistics_passes_the_hosts_nine_bytes_through():
    raw = sibling.configure_statistics(1, 0x11223344, 0x55667788)
    assert raw == bytes.fromhex("05 0d 01 44 33 22 11 88 77 66 55")
