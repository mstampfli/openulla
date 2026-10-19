"""The controller's procedures, state by state and event by event."""
import pytest

from openulla.codes import HciError, Mode, RejectReason, Result
from openulla.errors import ProtocolError
from openulla.procedure import (
    DEFAULT_PARAMETERS,
    HANDLER_ADDRESSES,
    SINK_HANDLER_ADDRESSES,
    THIRD_HANDLER_ADDRESSES,
    THREE_FLAGS,
    Action,
    Event,
    Flag,
    Procedure,
    SinkProcedure,
    SinkState,
    State,
    ThirdProcedure,
    ThirdState,
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
    assert step.after == State.AWAIT_TEARDOWN_BEGIN
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.result) == (Mode.ULLA_ON, Result.SUCCESS)
    step = procedure.feed(Event.TEARDOWN_BEGIN)
    assert (step.after, step.action) == (State.AWAIT_TEARDOWN_ACCEPTED, Action.REGISTER_CALLBACK)
    assert procedure.path_reported() is None  # one path only, no event yet
    step = procedure.path_reported(second=True)
    assert step is not None and step.event == Event.TEARDOWN_ACCEPTED
    assert step.after == State.AWAIT_TEARDOWN_ACCEPTED  # the link flag is still missing
    step = procedure.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.action) == (State.AWAIT_TEARDOWN_STEP, Action.REGISTER_CALLBACKS)
    assert procedure.flags & THREE_FLAGS == THREE_FLAGS
    step = procedure.feed(Event.CALLBACK_A)
    assert step.after == State.AWAIT_TEARDOWN_DONE


def test_the_two_paths_can_also_be_reported_in_one_call():
    procedure = Procedure(state=State.AWAIT_TEARDOWN_ACCEPTED, flags=int(Flag.LINK))
    step = procedure.path_reported(both=True)
    assert step is not None
    assert (step.after, step.action) == (State.AWAIT_TEARDOWN_STEP, Action.REGISTER_CALLBACKS)


def test_the_second_entry_starts_at_state_four():
    procedure = Procedure()
    step = procedure.begin_teardown()
    assert (step.before, step.after) == (State.AWAIT_TEARDOWN_BEGIN, State.AWAIT_TEARDOWN_ACCEPTED)


def test_an_unexpected_event_is_kept_where_it_is():
    procedure = Procedure()
    for event in (Event.TEARDOWN_COMPLETE, Event.CALLBACK_A, Event.MODULE_RESULT):
        step = procedure.feed(event)
        assert (step.after, step.expected) == (State.SEND_SETUP, False)
    procedure.state = State.AWAIT_TEARDOWN_DONE
    step = procedure.feed(Event.BEGIN)
    assert (step.after, step.expected) == (State.AWAIT_TEARDOWN_DONE, False)


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
    procedure.state = State.AWAIT_TEARDOWN_DONE
    assert procedure.feed(Event.CALLBACK_B).after == State.AWAIT_TEARDOWN_DONE
    step = procedure.feed(Event.TEARDOWN_COMPLETE)
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


# the sink's machine ---------------------------------------------------------------------


def test_each_table_is_as_long_as_its_dispatcher_indexes():
    assert set(SINK_HANDLER_ADDRESSES) == set(SinkState)
    assert len(HANDLER_ADDRESSES) == 9
    assert len(SINK_HANDLER_ADDRESSES) == 8
    assert len(THIRD_HANDLER_ADDRESSES) == 7
    # The three tables are consecutive runs of words, in this order.
    assert max(SINK_HANDLER_ADDRESSES.values()) < min(THIRD_HANDLER_ADDRESSES.values())
    assert max(THIRD_HANDLER_ADDRESSES.values()) < min(HANDLER_ADDRESSES.values())
    assert set(THIRD_HANDLER_ADDRESSES) == set(ThirdState)


def test_the_sink_forwards_to_its_sibling_and_waits_for_both_links():
    sink = SinkProcedure(handle=0x000B)
    step = sink.begin()
    assert (step.after, step.action) == (
        SinkState.AWAIT_BOTH_LINKS, Action.FORWARD_TO_SIBLING
    )
    sink.both_links_ready = False
    step = sink.feed(Event.SETUP_ACCEPTED)
    assert (step.after, step.action) == (SinkState.AWAIT_BOTH_LINKS, Action.NOTHING)
    assert sink.ready
    sink.both_links_ready = True
    step = sink.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.action) == (SinkState.AWAIT_MODULE_CALL, Action.SEND_ACCEPTED)


def test_the_sink_answers_alone_when_the_role_byte_says_so():
    sink = SinkProcedure(role_byte_is_one=True)
    step = sink.begin()
    assert (step.after, step.action) == (SinkState.AWAIT_MODULE_CALL, Action.CLAIM_SLOT)


def test_a_sibling_that_cannot_take_the_request_is_a_refusal_with_the_standard_code():
    sink = SinkProcedure(sibling_accepts=False)
    step = sink.begin()
    assert (step.after, step.action) == (SinkState.AWAIT_SETUP_REQUEST, Action.REFUSE)
    assert step.reason == HciError.LMP_PDU_NOT_ALLOWED == 0x24
    # And nothing is sent at all when the link can no longer carry a reply.
    quiet = SinkProcedure(sibling_accepts=False, can_answer=False)
    step = quiet.begin()
    assert (step.after, step.action) == (SinkState.AWAIT_SETUP_REQUEST, Action.NOTHING)
    assert step.reason is None


def test_the_sinks_whole_run_to_the_established_state():
    sink = SinkProcedure(handle=5, role_byte_is_one=True)
    sink.begin()
    step = sink.feed(Event.MODULE_CALL)
    assert (step.after, step.action) == (
        SinkState.AWAIT_MODULE_RESULT, Action.BUILD_REQUEST_BLOCK
    )
    step = sink.feed(Event.MODULE_RESULT)
    assert step.after == SinkState.AWAIT_SETUP_REQUEST  # a responder has nothing left to drive
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.result) == (Mode.ULLA_ON, Result.SUCCESS)
    step = sink.begin_teardown()
    assert step.after == SinkState.AWAIT_TEARDOWN_ACCEPTED
    step = sink.feed(Event.TEARDOWN_ACCEPTED)
    assert (step.after, step.action) == (SinkState.AWAIT_TEARDOWN_STEP, Action.REGISTER_CALLBACK)
    assert sink.feed(Event.CALLBACK_A).after == SinkState.AWAIT_TEARDOWN_DONE
    step = sink.feed(Event.CALLBACK_B)
    assert (step.after, step.action) == (SinkState.AWAIT_SETUP_REQUEST, Action.TEAR_DOWN)
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.result) == (Mode.BT_ACL_ACTIVE, Result.SUCCESS)


def test_the_sink_resends_the_accepted_pdu_on_the_link_event():
    sink = SinkProcedure(state=SinkState.AWAIT_MODULE_CALL)
    step = sink.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.action) == (SinkState.AWAIT_MODULE_CALL, Action.SEND_ACCEPTED)


def test_a_failed_module_result_keeps_the_sink_where_it_is():
    sink = SinkProcedure(state=SinkState.AWAIT_MODULE_RESULT, module_result=0)
    step = sink.feed(Event.MODULE_RESULT)
    assert step.after == SinkState.AWAIT_MODULE_RESULT
    assert step.host_event is not None
    assert step.host_event.result == Result.ERROR


def test_the_sink_keeps_its_state_on_an_event_it_does_not_expect():
    sink = SinkProcedure(state=SinkState.AWAIT_TEARDOWN_STEP)
    step = sink.feed(Event.TEARDOWN_COMPLETE)
    assert (step.after, step.expected) == (SinkState.AWAIT_TEARDOWN_STEP, False)


def test_the_sinks_dispatcher_also_refuses_to_run_inside_itself():
    sink = SinkProcedure(busy=True)
    with pytest.raises(ProtocolError):
        sink.feed(Event.BEGIN)


# the third role's machine ---------------------------------------------------------------


def test_the_third_machine_is_the_sinks_with_one_state_fewer():
    """Every event number after the first is one lower. [protocol.md 5.10]"""
    third = ThirdProcedure(handle=0x000B)
    step = third.begin()
    assert (step.after, step.action) == (ThirdState.AWAIT_MODULE_CALL, Action.CLAIM_SLOT)
    step = third.feed(Event.SETUP_ACCEPTED)  # the sink answers this with its event 2
    assert (step.after, step.action) == (ThirdState.AWAIT_MODULE_CALL, Action.SEND_ACCEPTED)
    step = third.feed(Event.LINK_STATE_ADVANCED)
    assert (step.after, step.action) == (
        ThirdState.AWAIT_MODULE_RESULT, Action.BUILD_REQUEST_BLOCK
    )
    step = third.feed(Event.MODULE_CALL)
    assert step.after == ThirdState.AWAIT_SETUP_REQUEST
    assert step.host_event is not None
    assert (step.host_event.mode, step.host_event.handle) == (Mode.ULLA_ON, 0x000B)


def test_the_third_machines_second_half_runs_one_event_lower_too():
    third = ThirdProcedure(handle=2, stop_reason=0xAA)
    step = third.begin_teardown()
    assert (step.before, step.after) == (
        ThirdState.AWAIT_TEARDOWN_BEGIN, ThirdState.AWAIT_TEARDOWN_ACCEPTED
    )
    step = third.feed(Event.TEARDOWN_BEGIN)
    assert (step.after, step.action) == (ThirdState.AWAIT_TEARDOWN_STEP, Action.REGISTER_CALLBACK)
    assert third.feed(Event.TEARDOWN_ACCEPTED).after == ThirdState.AWAIT_TEARDOWN_DONE
    step = third.feed(Event.CALLBACK_A)
    assert (step.after, step.action) == (ThirdState.AWAIT_SETUP_REQUEST, Action.TEAR_DOWN)
    assert step.host_event is not None
    assert step.host_event.reason == RejectReason.ULLA_LITE_UTP_DISCONNECTED


def test_the_third_machine_keeps_its_state_on_anything_else():
    third = ThirdProcedure(state=ThirdState.AWAIT_MODULE_RESULT, module_result=0)
    step = third.feed(Event.MODULE_CALL)
    assert step.after == ThirdState.AWAIT_MODULE_RESULT
    assert step.host_event is not None and step.host_event.result == Result.ERROR
    step = third.feed(Event.TEARDOWN_COMPLETE)
    assert (step.after, step.expected) == (ThirdState.AWAIT_MODULE_RESULT, False)
    with pytest.raises(ProtocolError):
        ThirdProcedure(busy=True).feed(Event.BEGIN)


# the accept sets of 5.9 and 5.10, state by state -----------------------------------------

SOURCE_ACCEPTS = {
    State.SEND_SETUP: {Event.BEGIN},
    State.AWAIT_SETUP_ACCEPTED: {Event.SETUP_ACCEPTED, Event.LINK_STATE_ADVANCED},
    State.AWAIT_MODULE_CALL: {Event.MODULE_CALL},
    State.AWAIT_MODULE_RESULT: {Event.MODULE_RESULT},
    State.AWAIT_TEARDOWN_BEGIN: {Event.TEARDOWN_BEGIN},
    State.AWAIT_TEARDOWN_ACCEPTED: {Event.LINK_STATE_ADVANCED, Event.TEARDOWN_ACCEPTED},
    State.RESET_ONLY: set(Event),
    State.AWAIT_TEARDOWN_STEP: {Event.CALLBACK_A, Event.CALLBACK_B},
    State.AWAIT_TEARDOWN_DONE: {Event.CALLBACK_A, Event.CALLBACK_B, Event.TEARDOWN_COMPLETE},
}
SINK_ACCEPTS = {
    SinkState.AWAIT_SETUP_REQUEST: {Event.BEGIN},
    SinkState.AWAIT_BOTH_LINKS: {Event.SETUP_ACCEPTED, Event.LINK_STATE_ADVANCED},
    SinkState.AWAIT_MODULE_CALL: {Event.LINK_STATE_ADVANCED, Event.MODULE_CALL},
    SinkState.AWAIT_MODULE_RESULT: {Event.MODULE_RESULT},
    SinkState.AWAIT_TEARDOWN_BEGIN: {Event.TEARDOWN_BEGIN},
    SinkState.AWAIT_TEARDOWN_ACCEPTED: {Event.TEARDOWN_ACCEPTED},
    SinkState.AWAIT_TEARDOWN_STEP: {Event.CALLBACK_A},
    SinkState.AWAIT_TEARDOWN_DONE: {Event.CALLBACK_B},
}
THIRD_ACCEPTS = {
    ThirdState.AWAIT_SETUP_REQUEST: {Event.BEGIN},
    ThirdState.AWAIT_MODULE_CALL: {Event.SETUP_ACCEPTED, Event.LINK_STATE_ADVANCED},
    ThirdState.AWAIT_MODULE_RESULT: {Event.MODULE_CALL},
    ThirdState.AWAIT_TEARDOWN_BEGIN: {Event.MODULE_RESULT},
    ThirdState.AWAIT_TEARDOWN_ACCEPTED: {Event.TEARDOWN_BEGIN},
    ThirdState.AWAIT_TEARDOWN_STEP: {Event.TEARDOWN_ACCEPTED},
    ThirdState.AWAIT_TEARDOWN_DONE: {Event.CALLBACK_A},
}


def test_every_source_state_expects_exactly_what_the_table_says():
    """And an unexpected event never moves it, except where the firmware resets. [5.9]"""
    for state, accepted in SOURCE_ACCEPTS.items():
        for event in Event:
            step = Procedure(state=state).feed(event)
            assert step.expected == (event in accepted), (state, event)
            if not step.expected:
                assert step.after in (state, State.SEND_SETUP), (state, event)


def test_every_sink_state_expects_exactly_what_the_table_says():
    for state, accepted in SINK_ACCEPTS.items():
        for event in Event:
            step = SinkProcedure(state=state).feed(event)
            assert step.expected == (event in accepted), (state, event)
            if not step.expected:
                assert step.after == state, (state, event)


def test_every_third_state_expects_exactly_what_the_table_says():
    for state, accepted in THIRD_ACCEPTS.items():
        for event in Event:
            step = ThirdProcedure(state=state).feed(event)
            assert step.expected == (event in accepted), (state, event)
            if not step.expected:
                assert step.after == state, (state, event)


def test_feed_all_walks_every_machine():
    """The three dispatchers' batch entry, which the single-step tests do not reach."""
    source = Procedure()
    assert [s.after for s in source.feed_all([Event.BEGIN, Event.SETUP_ACCEPTED])] == [
        State.AWAIT_SETUP_ACCEPTED, State.AWAIT_MODULE_CALL
    ]
    sink = SinkProcedure(role_byte_is_one=True)
    assert [s.after for s in sink.feed_all([Event.BEGIN, Event.MODULE_CALL])] == [
        SinkState.AWAIT_MODULE_CALL, SinkState.AWAIT_MODULE_RESULT
    ]
    third = ThirdProcedure()
    assert [s.after for s in third.feed_all([Event.BEGIN, Event.SETUP_ACCEPTED])] == [
        ThirdState.AWAIT_MODULE_CALL, ThirdState.AWAIT_MODULE_CALL
    ]
