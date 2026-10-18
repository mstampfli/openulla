"""The session manager's table: its shape, its two branches, and a whole session walked."""
from openulla.session import (
    HANDLER_OK,
    TRANSITIONS,
    Event,
    Machine,
    State,
    row_for,
)


def test_no_state_answers_the_same_event_twice():
    """The firmware takes the first row it finds, so two rows for one pair would be ambiguous."""
    seen = set()
    for row in TRANSITIONS:
        for event in row.events:
            assert (row.state, event) not in seen
            seen.add((row.state, event))


def test_every_state_up_to_host_teardown_has_rows_and_the_abort_state_has_none():
    with_rows = {row.state for row in TRANSITIONS}
    assert with_rows == {State(i) for i in range(0, 15)}
    assert State.WAIT_ABORT_RSP not in with_rows
    assert State.NO_CHANGE not in with_rows


def test_an_event_with_no_row_is_ignored_and_the_state_does_not_change():
    machine = Machine(State.IDLE)
    step = machine.feed(Event.ULLA_LINK_UP)
    assert not step.handled and not step.changed and machine.state == State.IDLE


def test_the_no_change_marker_means_stay():
    machine = Machine(State.HOST_SETUP)
    step = machine.feed(Event.SIBLING_AUDIO_CONFIGURED)
    assert step.handled and machine.state == State.HOST_SETUP
    assert row_for(State.HOST_SETUP, Event.SIBLING_AUDIO_CONFIGURED).ok == State.NO_CHANGE


def test_the_handler_result_picks_which_of_the_two_next_states_is_taken():
    ok = Machine(State.HOST_SETUP)
    ok.on(State.HOST_SETUP, Event.SUPERVISION_TIMEOUT_UPDATED, lambda m, e: HANDLER_OK)
    assert ok.feed(Event.SUPERVISION_TIMEOUT_UPDATED).after == State.WAIT_SESSION_RSP
    other = Machine(State.HOST_SETUP)
    other.on(State.HOST_SETUP, Event.SUPERVISION_TIMEOUT_UPDATED, lambda m, e: 0)
    assert other.feed(Event.SUPERVISION_TIMEOUT_UPDATED).after == State.WAIT_AUDIO_QUIESCE


def test_a_whole_session_walks_from_init_to_stream_open_and_back():
    machine = Machine(State.INIT)
    path = [
        (Event.INIT, State.IDLE),
        (Event.SESSION_START_REQ, State.HOST_SETUP),
        (Event.APP_SETUP_COMPLETE, State.HOST_SETUP),
        (Event.SUPERVISION_TIMEOUT_UPDATED, State.WAIT_SESSION_RSP),
        (Event.PEER_SESSION_START_RSP, State.WAIT_AUDIO_QUIESCE),
        (Event.LOCAL_FLOW_OFF_DONE, State.CONTROLLER_SETUP),
        (Event.ULLA_LINK_UP, State.DSP_SETUP),
        (Event.DSP_SESSION_READY, State.DSP_SETUP),
        (Event.PEER_DSP_CONFIG_COMPLETE, State.DSP_SETUP),
        (Event.DSP_SETUP_COMPLETE, State.SESSION_OPEN),
        (Event.HOST_STREAM_START_REQ, State.STREAM_HOST_SETUP),
        (Event.STREAM_READY_FROM_DSP, State.STREAM_HOST_SETUP),
        (Event.STREAM_READY_FROM_CONTROLLER, State.STREAM_HOST_SETUP),
        (Event.PEER_STREAM_START_RSP, State.STREAM_HOST_SETUP),
        (Event.STREAM_HOST_SETUP_COMPLETE, State.STREAM_OPEN),
        (Event.HOST_STREAM_STOP_REQ, State.WAIT_AUDIO_STOPPED),
        (Event.PEER_STREAM_STOP_RSP, State.SESSION_OPEN),
        (Event.HOST_SESSION_STOP_REQ, State.WAIT_STOP_RSP),
        (Event.PEER_SESSION_STOP_RSP, State.WAIT_AUDIO_QUIESCE),
    ]
    for event, expected in path:
        assert machine.feed(event).after == expected, event.name


def test_a_link_that_goes_down_lands_in_idle_or_in_host_teardown():
    for state in (
        State.CONTROLLER_SETUP,
        State.DSP_SETUP,
        State.SESSION_OPEN,
        State.STREAM_HOST_SETUP,
        State.STREAM_OPEN,
        State.WAIT_AUDIO_STOPPED,
        State.STREAM_HOST_TEARDOWN,
        State.WAIT_STOP_RSP,
        State.CONTROLLER_TEARDOWN,
    ):
        row = row_for(state, Event.ULLA_LINK_DOWN)
        assert row is not None and row.handler == 0x26308
        assert (row.ok, row.other) == (State.IDLE, State.HOST_TEARDOWN)


def test_disconnect_and_abort_share_one_row_wherever_both_are_answered():
    for state in (State.HOST_SETUP, State.WAIT_SESSION_RSP, State.WAIT_AUDIO_QUIESCE):
        row = row_for(state, Event.DISCONNECT)
        assert row is not None and Event.ABORT in row.events


def test_host_teardown_returns_to_idle_on_either_branch_of_a_disconnect():
    machine = Machine(State.HOST_TEARDOWN)
    machine.on(State.HOST_TEARDOWN, Event.DISCONNECT, lambda m, e: 0)
    assert machine.feed(Event.DISCONNECT).after == State.IDLE


def test_the_history_records_what_was_fed_and_what_it_did():
    machine = Machine(State.IDLE)
    machine.feed(Event.RESET)
    machine.feed(Event.ULLA_LINK_UP)
    assert [step.handled for step in machine.history] == [True, False]
