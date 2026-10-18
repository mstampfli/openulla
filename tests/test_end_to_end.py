"""A whole setup, driven against the simulated earbud: every layer in one run."""
import pytest

from openulla import aap, sibling, sim
from openulla.codes import AapReject, Mode, RejectReason, Result, SessionStatus
from openulla.controller import AcceptChecks, Responder
from openulla.errors import Refused
from openulla.hci import UllaSetup
from openulla.session import Event, State
from openulla.sink import Sink

SETUP = UllaSetup(handle=0x000B, extra_flags=0, config_word=0x0100, flags=0x03)


def test_the_whole_setup_reaches_an_open_session_and_then_an_open_stream():
    link = sim.connect()
    reply = link.source.open_service(timeout=None)
    assert (reply.major, reply.minor) == (1, 3)
    assert link.sink.state == State.IDLE

    link.source.start_session(mode=0)
    assert link.sink.state == State.HOST_SETUP
    link.feed_all(Event.APP_SETUP_COMPLETE, Event.SUPERVISION_TIMEOUT_UPDATED)
    assert link.sink.state == State.WAIT_SESSION_RSP
    link.feed(Event.PEER_SESSION_START_RSP)
    assert link.source.read_status(timeout=None) == SessionStatus.OK
    assert link.sink.state == State.WAIT_AUDIO_QUIESCE

    link.quiesce()
    assert link.sink.state == State.CONTROLLER_SETUP
    event = link.controller_setup(SETUP)
    assert (event.mode, event.result, event.reason) == (
        Mode.ULLA_ON,
        Result.SUCCESS,
        RejectReason.NOT_APPLICABLE,
    )
    assert link.sink.state == State.DSP_SETUP
    assert link.finish_setup() == State.SESSION_OPEN

    link.source.start_stream()
    link.feed_all(
        Event.STREAM_READY_FROM_DSP,
        Event.STREAM_READY_FROM_CONTROLLER,
        Event.PEER_STREAM_START_RSP,
        Event.STREAM_HOST_SETUP_COMPLETE,
    )
    assert link.sink.state == State.STREAM_OPEN

    link.source.stop_stream()
    link.feed(Event.PEER_STREAM_STOP_RSP)
    assert link.sink.state == State.SESSION_OPEN


def test_the_earbud_asks_its_sibling_with_the_hosts_address_reversed():
    link = sim.connect()
    link.source.open_service(timeout=None)
    link.source.start_session(mode=1)
    asked = [a for a in link.sink.actions if a.kind == "sibling"]
    assert asked, "the manager tells its sibling before anything else"
    parsed = sibling.parse_start_session_request(asked[0].data)
    assert parsed.address == link.peer and parsed.mode == 1


def test_an_earbud_whose_sibling_transport_is_down_answers_the_documented_status():
    link = sim.connect(sink=Sink(sibling_active=False))
    link.source.open_service(timeout=None)
    link.source.start_session()
    assert link.source.read_status(timeout=None) == SessionStatus.SIBLING_TRANSPORT_NOT_READY
    assert link.sink.state == State.IDLE


def test_a_second_start_while_a_session_runs_is_ignored_in_silence():
    link = sim.connect()
    link.source.open_service(timeout=None)
    link.source.start_session()
    before = link.sink.state
    link.source.start_session()
    assert link.sink.state == before
    assert link.transport.pending() == 0


def test_a_peer_that_refuses_the_setup_leaves_the_manager_where_it_was():
    link = sim.connect()
    link.open_and_start()
    link.quiesce()
    assert link.sink.state == State.CONTROLLER_SETUP
    event = link.controller_setup(SETUP, responder=Responder(checks=AcceptChecks(busy=True)))
    assert event.result == Result.SETUP_REJECTED_BY_PEER
    assert event.reason == RejectReason.SECONDARY_DISALLOWED_ACTIVITY
    assert link.sink.state == State.CONTROLLER_SETUP


def test_subscribing_tells_the_sibling_and_the_local_controller():
    link = sim.connect()
    link.source.open_service(timeout=None)
    link.source.subscribe_reports()
    kinds = [a.kind for a in link.sink.actions]
    assert kinds.count("sibling") == 1 and kinds.count("controller") == 1
    assert aap.CLASS_ULLA_REPORTS in link.sink.sessions[link.peer].subscribed


def test_only_three_sessions_exist_and_a_channel_may_not_open_twice():
    sink = Sink()
    for n in range(3):
        sink.receive(aap.open_request(), peer=bytes((n, 0, 0, 0, 0, 0)))
    with pytest.raises(Refused) as fourth:
        sink.receive(aap.open_request(), peer=bytes((9, 0, 0, 0, 0, 0)))
    assert fourth.value.code == AapReject.NO_FREE_SESSION_SLOT
    with pytest.raises(Refused) as again:
        sink.receive(aap.open_request(), peer=bytes((0, 0, 0, 0, 0, 0)))
    assert again.value.code == AapReject.SESSION_EXISTS


def test_a_short_open_request_is_refused_with_the_firmwares_code():
    with pytest.raises(Refused) as refused:
        Sink().receive(aap.open_request()[:12])
    assert refused.value.code == AapReject.FRAME_TOO_SHORT


def test_closing_the_channel_resets_the_session():
    link = sim.connect()
    link.open_and_start()
    link.transport.close()
    assert link.sink.state == State.IDLE
    assert link.peer not in link.sink.sessions
    link.source.open_service(timeout=None)
    assert link.peer in link.sink.sessions


def test_the_transcript_holds_every_frame_and_pdu_of_the_run():
    link = sim.connect()
    link.open_and_start()
    link.quiesce()
    link.controller_setup(SETUP)
    lines = link.transcript()
    assert any("opcode 0x36" in line for line in lines)
    assert any("LMP e6" in line for line in lines)
    assert any("LMP 06 73" in line for line in lines)
