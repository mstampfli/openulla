"""The source's nine-state setup procedure, state by state and event by event."""
import pytest

from openulla.codes import Mode, RejectReason, Result
from openulla.errors import ProtocolError
from openulla.procedure import (
    DEFAULT_PARAMETERS,
    HANDLER_ADDRESSES,
    THREE_FLAGS,
    Action,
    Event,
    Flag,
    Procedure,
    State,
)


def test_there_is_one_handler_address_per_state():
    assert set(HANDLER_ADDRESSES) == set(State)
    assert len(set(HANDLER_ADDRESSES.values())) == 9
    # The table is a run of nine words, so the addresses are in state order.
    assert list(HANDLER_ADDRESSES.values()) == sorted(HANDLER_ADDRESSES.values())


def test_the_nominal_run_from_the_first_entry_to_the_established_state():
    procedure = Procedure(handle=0x000B)
    step = procedure.begin()
    assert (step.before, step.after, step.action) == (
        State.SEND_SETUP, State.AWAIT_SETUP_ACCEPTED, Action.SEND_SETUP_REQUEST
    )
    step = procedure.feed(Event.SETUP_ACCEPTED)
    assert (step.after, step.action) == (State.AWAIT_MODULE_CALL, Action.SEND_CONFIGURATION)
    step = procedure.feed(Event.MODULE_CALL)
    assert (step.after, step.action) == (State.AWAIT_MODULE_RESULT, Action.BUILD_REQUEST_BLOCK)
    step = procedure.feed(Event.MODULE_RESULT)
    assert step.after == State.AWAIT_SECOND_BEGIN
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.result) == (Mode.ULLA_ON, Result.SUCCESS)
    step = procedure.feed(Event.BEGIN_PHASE_TWO)
    assert (step.after, step.action) == (State.AWAIT_THREE_FLAGS, Action.REGISTER_CALLBACK)
    assert procedure.path_reported() is None  # one path only, no event yet
    step = procedure.path_reported(second=True)
    assert step is not None and step.event == Event.BOTH_PATHS_READY
    assert step.after == State.AWAIT_THREE_FLAGS  # the link flag is still missing
    step = procedure.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.action) == (State.AWAIT_CALLBACKS, Action.REGISTER_CALLBACKS)
    assert procedure.flags & THREE_FLAGS == THREE_FLAGS
    step = procedure.feed(Event.CALLBACK_A)
    assert step.after == State.ESTABLISHED


def test_the_two_paths_can_also_be_reported_in_one_call():
    procedure = Procedure(state=State.AWAIT_THREE_FLAGS, flags=int(Flag.LINK))
    step = procedure.path_reported(both=True)
    assert step is not None
    assert (step.after, step.action) == (State.AWAIT_CALLBACKS, Action.REGISTER_CALLBACKS)


def test_the_second_entry_starts_at_state_four():
    procedure = Procedure()
    step = procedure.begin_phase_two()
    assert (step.before, step.after) == (State.AWAIT_SECOND_BEGIN, State.AWAIT_THREE_FLAGS)


def test_an_unexpected_event_is_kept_where_it_is():
    procedure = Procedure()
    for event in (Event.STOP, Event.CALLBACK_A, Event.MODULE_RESULT):
        step = procedure.feed(event)
        assert (step.after, step.expected) == (State.SEND_SETUP, False)
    procedure.state = State.ESTABLISHED
    step = procedure.feed(Event.BEGIN)
    assert (step.after, step.expected) == (State.ESTABLISHED, False)


def test_the_state_that_waits_for_the_answer_needs_both_conditions():
    """Event 1 alone is not enough while the link test says no. [protocol.md 5.9]"""
    procedure = Procedure(link_test=False)
    procedure.begin()
    step = procedure.feed(Event.SETUP_ACCEPTED)
    assert (step.after, step.action) == (State.AWAIT_SETUP_ACCEPTED, Action.NOTHING)
    assert procedure.flags & Flag.ACCEPTED
    procedure.link_test = True
    step = procedure.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.action) == (State.AWAIT_MODULE_CALL, Action.SEND_CONFIGURATION)


def test_the_link_event_alone_does_nothing_until_the_peer_has_accepted():
    procedure = Procedure()
    procedure.begin()
    step = procedure.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.expected) == (State.AWAIT_SETUP_ACCEPTED, True)
    assert not procedure.flags & Flag.ACCEPTED
    assert procedure.feed(Event.SETUP_ACCEPTED).after == State.AWAIT_MODULE_CALL


def test_a_failed_module_result_reports_an_error_and_goes_back_to_the_start():
    procedure = Procedure(handle=7, module_result=0)
    procedure.state = State.AWAIT_MODULE_RESULT
    step = procedure.feed(Event.MODULE_RESULT)
    assert step.after == State.SEND_SETUP
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.result, step.host_event.reason) == (
        Mode.BT_ACL_ACTIVE, Result.ERROR, RejectReason.NOT_APPLICABLE
    )
    assert step.host_event.handle == 7


def test_the_teardown_reports_success_with_the_stored_reason():
    procedure = Procedure(handle=3, stop_reason=0xAA)
    procedure.state = State.ESTABLISHED
    assert procedure.feed(Event.CALLBACK_B).after == State.ESTABLISHED
    step = procedure.feed(Event.STOP)
    assert (step.after, step.action) == (State.SEND_SETUP, Action.TEAR_DOWN)
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.result, step.host_event.reason) == (
        Mode.BT_ACL_ACTIVE, Result.SUCCESS, RejectReason.ULLA_LITE_UTP_DISCONNECTED
    )


def test_state_six_answers_every_event_with_zero():
    procedure = Procedure()
    for event in Event:
        procedure.state = State.RESET_ONLY
        assert procedure.feed(event).after == State.SEND_SETUP


def test_a_refusal_is_not_an_event_and_leaves_the_state_alone():
    procedure = Procedure(handle=0x000B)
    procedure.begin()
    event = procedure.peer_refused(RejectReason.PRIMARY_DISALLOWED_ACTIVITY)
    assert (event.mode, event.result) == (Mode.BT_ACL_ACTIVE, Result.SETUP_REJECTED_BY_PEER)
    assert event.reason == 0x83
    assert procedure.state == State.AWAIT_SETUP_ACCEPTED
    assert procedure.history[-1].event == Event.BEGIN
    with pytest.raises(ProtocolError):
        procedure.peer_refused(0x100)


def test_a_lost_link_reports_the_result_that_matches_the_phase():
    procedure = Procedure(handle=1)
    assert procedure.link_lost(first_phase=True).result == Result.FAIL_TO_BE_ESTABLISHED
    assert procedure.link_lost(first_phase=False).result == Result.LSTO_ERROR


def test_the_dispatcher_refuses_to_run_inside_itself():
    procedure = Procedure()
    procedure.flags |= Flag.BUSY
    with pytest.raises(ProtocolError):
        procedure.feed(Event.BEGIN)


def test_beginning_again_clears_only_the_five_low_flag_bits():
    procedure = Procedure(flags=0xFF)
    procedure.begin()
    assert procedure.flags == 0xE0


def test_the_history_keeps_every_step_in_order():
    procedure = Procedure()
    procedure.begin()
    procedure.feed_all([Event.SETUP_ACCEPTED, Event.MODULE_CALL])
    assert [step.event for step in procedure.history] == [
        Event.BEGIN, Event.SETUP_ACCEPTED, Event.MODULE_CALL
    ]
    assert [step.after for step in procedure.history] == [
        State.AWAIT_SETUP_ACCEPTED, State.AWAIT_MODULE_CALL, State.AWAIT_MODULE_RESULT
    ]


def test_the_four_parameters_are_the_initialisers_own_defaults():
    assert DEFAULT_PARAMETERS == (3, 8, 8, 8)
