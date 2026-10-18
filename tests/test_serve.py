"""The sink service: what a source talking to it over a channel actually gets back."""
import pytest

from openulla import aap, serve
from openulla.codes import SessionStatus
from openulla.session import State
from openulla.sink import Sink

PEER = bytes.fromhex("112233445566")


def service(**kwargs):
    return serve.Service(sink=Sink(), peer=PEER, **kwargs)


def test_a_source_gets_as_far_as_an_open_session():
    s = service()
    assert aap.OpenReply.from_bytes(
        aap.parse_frame(s.handle(aap.open_request())[0]).body
    ).ok
    replies = s.handle(aap.start_session(0))
    assert [aap.parse_status(aap.parse_frame(r).body) for r in replies] == [SessionStatus.OK]
    assert s.sink.state == State.SESSION_OPEN
    assert s.handle(aap.start_stream()) == []
    assert s.sink.state == State.STREAM_OPEN
    assert s.handle(aap.stop_stream()) == []
    assert s.sink.state == State.SESSION_OPEN


def test_a_passive_service_leaves_the_machine_where_the_host_put_it():
    s = service(advance=False)
    s.handle(aap.open_request())
    assert s.handle(aap.start_session(0)) == []
    assert s.sink.state == State.HOST_SETUP


def test_every_simulated_event_is_logged_by_name():
    lines = []
    s = service(log=lines.append)
    s.handle(aap.open_request())
    s.handle(aap.start_session(0))
    named = " ".join(lines)
    for event in serve.AFTER_START:
        assert event.name in named


def test_a_refusal_is_logged_and_answered_with_silence():
    lines = []
    s = service(log=lines.append)
    assert s.handle(aap.open_request()[:12]) == []
    assert "refused with 0x72" in lines[0]


def test_a_frame_the_sink_does_not_answer_is_logged_too():
    lines = []
    s = service(log=lines.append)
    s.handle(aap.open_request())
    assert s.handle(aap.data_frame(0x99)) == []
    assert "not a frame this sink answers" in lines[-1]


def test_closing_the_channel_resets_the_session():
    s = service()
    s.handle(aap.open_request())
    s.handle(aap.start_session(0))
    s.closed()
    assert s.sink.state == State.IDLE
    assert PEER not in s.sink.sessions


def test_the_stream_chain_only_runs_from_the_state_it_belongs_to():
    s = service()
    s.handle(aap.open_request())
    # A stream start before a session cannot advance anything.
    assert s.handle(aap.start_stream()) == []
    assert s.sink.state == State.IDLE


def test_the_service_needs_no_socket_at_all():
    """The whole thing is frames in, frames out, which is why it can be tested here."""
    s = service()
    with pytest.raises(AttributeError):
        s.sink.transport  # there is none
