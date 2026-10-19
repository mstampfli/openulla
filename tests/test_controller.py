"""The controller side: the four checks, their reasons, and what the host is told."""
import pytest

from openulla import lmp
from openulla.codes import Mode, RejectReason, Result
from openulla.controller import ACCEPT, PRIMARY_ROLE, AcceptChecks, Initiator, Responder
from openulla.errors import ProtocolError
from openulla.hci import UllaSetup
from openulla.procedure import Event, Procedure, SinkProcedure, SinkState
from openulla.procedure import State as procedure_state

SETUP = UllaSetup(handle=0x000B, extra_flags=0x01, config_word=0x0102, flags=0xFF)


def test_the_checks_run_in_the_firmwares_order():
    """Each check is tested only once the one before it has passed, and the reason it gives
    is the primary-role half of the list, because bit 7 is set before any check runs."""
    assert AcceptChecks(feature_enabled=False, object_ready=False, busy=True).decide() == (
        RejectReason.PRIMARY_ROLE_NOT_INITIATED
    )
    assert AcceptChecks(object_ready=False, busy=True).decide() == (
        RejectReason.PRIMARY_ULLA_ALREADY_CONNECTED
    )
    assert AcceptChecks(busy=True, link_state=6).decide() == (
        RejectReason.PRIMARY_DISALLOWED_ACTIVITY
    )
    assert AcceptChecks(link_state=6).decide() == (
        RejectReason.PRIMARY_INVALID_SOURCE_CONNECTION
    )
    assert AcceptChecks().decide() == ACCEPT


def test_the_role_bit_is_what_makes_them_the_primary_half():
    for nibble, reason in (
        (1, RejectReason.PRIMARY_ROLE_NOT_INITIATED),
        (2, RejectReason.PRIMARY_ULLA_ALREADY_CONNECTED),
        (3, RejectReason.PRIMARY_DISALLOWED_ACTIVITY),
        (4, RejectReason.PRIMARY_UTP_NOT_IN_ACTIVE_MODE),
        (5, RejectReason.PRIMARY_ILLEGAL_NUM_CONNECTIONS),
        (6, RejectReason.PRIMARY_INVALID_SOURCE_CONNECTION),
    ):
        assert reason == PRIMARY_ROLE | nibble
    # 2 is the one the secondary half has no name for, and it is check 2's own number.
    assert 2 not in {reason.value for reason in RejectReason}
    assert AcceptChecks(object_ready=False).decide() == PRIMARY_ROLE | 2


def test_the_link_state_check_returns_only_the_reasons_it_can():
    for state in (0, 4, 5, 6):
        AcceptChecks(link_state=state).decide()
    with pytest.raises(ProtocolError):
        AcceptChecks(link_state=7).decide()


def test_an_accepted_setup_is_answered_with_the_accepted_pdu():
    responder = Responder()
    reply = responder.receive(Initiator().setup_request(SETUP))
    assert lmp.parse(reply) == lmp.Accepted(lmp.OPCODE_ULLA_SETUP)
    assert responder.received is not None
    assert responder.forwarded == responder.received.to_bytes()


def test_a_refused_setup_names_the_opcode_and_the_reason():
    responder = Responder(checks=AcceptChecks(busy=True))
    reply = lmp.parse(responder.receive(Initiator().setup_request(SETUP)))
    assert reply == lmp.NotAccepted(
        lmp.OPCODE_ULLA_SETUP, RejectReason.PRIMARY_DISALLOWED_ACTIVITY
    )
    assert responder.received is None


def test_the_sink_holds_the_acceptance_until_its_sibling_link_is_ready():
    responder = Responder(sibling_ready=False)
    assert responder.receive(Initiator().setup_request(SETUP)) == b""
    assert responder.forwarded is not None
    assert lmp.parse(responder.sibling_became_ready()) == lmp.Accepted(lmp.OPCODE_ULLA_SETUP)


def test_the_initiator_maps_the_commands_parameters_the_way_the_firmware_does():
    """The mapping of protocol.md 5.1, parameter by parameter."""
    pdu = lmp.SetupRequest.from_bytes(Initiator(radio_value=0x01020304).setup_request(SETUP))
    assert pdu.config_word == SETUP.config_word  # parameters 3 and 4, passed through
    assert pdu.flags == SETUP.flags & 0b11  # bits 0 and 1 of parameter 5
    assert pdu.extra_flags == (SETUP.flags >> 3) & 1  # its bit 3, as a whole byte
    assert pdu.radio_value == 0x01020304  # the radio read, not a parameter
    # Parameter 2 is the byte the firmware writes past the declared length, so a nine-byte
    # PDU cannot carry it and this one does not pretend to.
    assert len(pdu.to_bytes()) == lmp.SetupRequest.LENGTH == 9
    for flags, expected in ((0, (0, 0)), (0b11, (3, 0)), (0b1000, (0, 1)), (0xFF, (3, 1))):
        built = lmp.SetupRequest.from_bytes(
            Initiator().setup_request(UllaSetup(1, 0, 0, flags))
        )
        assert (built.flags, built.extra_flags) == expected, flags


def test_the_transaction_id_of_the_answer_follows_the_request():
    responder = Responder()
    reply = lmp.parse(responder.receive(Initiator(tid=1).setup_request(SETUP)))
    assert reply.tid == 1


def test_the_host_sees_a_rejection_as_the_peers_reason():
    initiator = Initiator()
    initiator.setup_request(SETUP)
    answer = initiator.on_reply(lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, 5).to_bytes())
    assert answer.pdu == b""
    event = answer.host_event
    assert event is not None
    assert (event.mode, event.result, event.reason) == (
        Mode.BT_ACL_ACTIVE,
        Result.SETUP_REJECTED_BY_PEER,
        5,
    )
    assert event.handle == SETUP.handle


def test_an_accepted_setup_sends_the_configuration_and_tells_the_host_nothing_yet():
    """The firmware reports the mode two states later, not here. [protocol.md 5.9]"""
    initiator = Initiator()
    initiator.setup_request(SETUP)
    answer = initiator.on_reply(lmp.Accepted(lmp.OPCODE_ULLA_SETUP).to_bytes())
    assert answer.host_event is None
    assert lmp.parse(answer.pdu) == lmp.Configuration(0, 0)
    assert initiator.procedure.state is procedure_state.AWAIT_MODULE_CALL
    # And then the step a source has to drive itself, which is where the host hears.
    event = initiator.on_module_result()
    assert (event.mode, event.result) == (Mode.ULLA_ON, Result.SUCCESS)
    assert initiator.procedure.state is procedure_state.AWAIT_TEARDOWN_BEGIN


def test_a_module_that_does_not_answer_with_one_is_an_error_to_the_host():
    initiator = Initiator()
    initiator.setup_request(SETUP)
    initiator.on_reply(lmp.Accepted(lmp.OPCODE_ULLA_SETUP).to_bytes())
    event = initiator.on_module_result(ok=False)
    assert (event.mode, event.result) == (Mode.BT_ACL_ACTIVE, Result.ERROR)
    assert initiator.procedure.state is procedure_state.SEND_SETUP  # back to the start


def test_the_initiator_sends_nothing_while_its_link_test_has_not_passed():
    initiator = Initiator(procedure=Procedure(link_test=False))
    initiator.setup_request(SETUP)
    answer = initiator.on_reply(lmp.Accepted(lmp.OPCODE_ULLA_SETUP).to_bytes())
    assert (answer.pdu, answer.host_event) == (b"", None)


def test_an_answer_about_another_opcode_is_refused():
    initiator = Initiator()
    initiator.setup_request(SETUP)
    with pytest.raises(ProtocolError):
        initiator.on_reply(lmp.Accepted(0x74).to_bytes())
    with pytest.raises(ProtocolError):
        initiator.on_reply(lmp.Configuration(0, 0).to_bytes())


def test_band_ranges_are_stored_per_band_by_the_responder():
    responder = Responder()
    responder.receive(lmp.BandRanges((lmp.BandRange(5, 29, 6), lmp.BandRange(7, 41, 7))).to_bytes())
    assert responder.band_ranges == {5: (29, 6), 7: (41, 7)}


def test_the_responder_answers_a_feature_request_with_its_own_word():
    responder = Responder(features=0x000000E0)
    reply = lmp.parse(responder.receive(lmp.Features(0x00000060).to_bytes()))
    assert reply == lmp.Features(0x000000E0, reply=True)
    assert responder.peer_features == 0x00000060
    assert lmp.agreed(responder.features, responder.peer_features, lmp.FEATURE_BIT_BANDS)


def test_the_responder_drives_the_firmwares_own_procedure():
    """One set of rules, not two: the states come from openulla.procedure. [protocol.md 5.10]"""
    responder = Responder()
    responder.receive(Initiator().setup_request(SETUP))
    assert responder.procedure.state is SinkState.AWAIT_MODULE_CALL
    assert [step.event for step in responder.procedure.history] == [
        Event.BEGIN, Event.SETUP_ACCEPTED
    ]


def test_a_request_the_sibling_cannot_take_is_refused_with_the_standard_code():
    responder = Responder(procedure=SinkProcedure(sibling_accepts=False))
    reply = lmp.parse(responder.receive(Initiator().setup_request(SETUP)))
    assert reply == lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, 0x24)
    assert responder.forwarded is None
    assert responder.procedure.state is SinkState.AWAIT_SETUP_REQUEST


def test_the_role_byte_path_answers_without_forwarding_anything():
    responder = Responder(procedure=SinkProcedure(role_byte_is_one=True), sibling_ready=False)
    reply = lmp.parse(responder.receive(Initiator().setup_request(SETUP)))
    assert reply == lmp.Accepted(lmp.OPCODE_ULLA_SETUP)
    assert responder.forwarded is None
    assert responder.procedure.state is SinkState.AWAIT_MODULE_CALL


def test_a_feature_reply_is_kept_and_not_answered():
    responder = Responder()
    assert responder.receive(lmp.Features(0x11223344, reply=True).to_bytes()) == b""
    assert responder.peer_features == 0x11223344


def test_a_received_configuration_advances_the_sinks_procedure():
    """Receiving 0x74 is what posts event 3 to a responder. [protocol.md 5.2, 5.9]"""
    responder = Responder()
    responder.receive(Initiator().setup_request(SETUP))
    assert responder.procedure.state is SinkState.AWAIT_MODULE_CALL
    assert responder.receive(lmp.Configuration(0, 0).to_bytes()) == b""
    assert responder.procedure.state is SinkState.AWAIT_MODULE_RESULT
    assert responder.procedure.history[-1].event == Event.MODULE_CALL
    # And a second one, out of that state, changes nothing and is not an error.
    assert responder.receive(lmp.Configuration(0, 0).to_bytes()) == b""
    assert responder.procedure.state is SinkState.AWAIT_MODULE_RESULT
