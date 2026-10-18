"""The controller side: the four checks, their reasons, and what the host is told."""
import pytest

from openulla import lmp
from openulla.codes import Mode, RejectReason, Result
from openulla.controller import ACCEPT, AcceptChecks, Initiator, Responder
from openulla.errors import ProtocolError
from openulla.hci import UllaSetup

SETUP = UllaSetup(handle=0x000B, extra_flags=0x01, config_word=0x0102, flags=0xFF)


def test_the_checks_run_in_the_firmwares_order():
    """Each check is tested only once the one before it has passed."""
    assert AcceptChecks(feature_enabled=False, object_ready=False, busy=True).decide() == 1
    assert AcceptChecks(object_ready=False, busy=True).decide() == 2
    assert AcceptChecks(busy=True, link_state=6).decide() == 3
    assert AcceptChecks(link_state=6).decide() == 6
    assert AcceptChecks().decide() == ACCEPT


def test_the_second_check_produces_the_reason_the_name_table_leaves_unnamed():
    assert AcceptChecks(object_ready=False).decide() == 2
    assert 2 not in {reason.value for reason in RejectReason}


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
    assert reply == lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, RejectReason.SECONDARY_DISALLOWED_ACTIVITY)
    assert responder.received is None


def test_the_sink_holds_the_acceptance_until_its_sibling_link_is_ready():
    responder = Responder(sibling_ready=False)
    assert responder.receive(Initiator().setup_request(SETUP)) == b""
    assert responder.forwarded is not None
    assert lmp.parse(responder.sibling_became_ready()) == lmp.Accepted(lmp.OPCODE_ULLA_SETUP)


def test_the_initiator_passes_only_the_flag_bits_the_command_gives_the_pdu():
    pdu = lmp.SetupRequest.from_bytes(Initiator(radio_value=0x01020304).setup_request(SETUP))
    assert pdu.flags == 0b1011  # bits 0, 1 and 3 of the command's flag byte
    assert pdu.config_word == 0x0102
    assert pdu.extra_flags == 0x01
    assert pdu.radio_value == 0x01020304


def test_the_transaction_id_of_the_answer_follows_the_request():
    responder = Responder()
    reply = lmp.parse(responder.receive(Initiator(tid=1).setup_request(SETUP)))
    assert reply.tid == 1


def test_the_host_sees_a_rejection_as_the_peers_reason():
    initiator = Initiator()
    initiator.setup_request(SETUP)
    event = initiator.on_reply(lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, 5).to_bytes())
    assert (event.mode, event.result, event.reason) == (
        Mode.BT_ACL_ACTIVE,
        Result.SETUP_REJECTED_BY_PEER,
        5,
    )
    assert event.handle == SETUP.handle


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


def test_a_feature_reply_is_kept_and_not_answered():
    responder = Responder()
    assert responder.receive(lmp.Features(0x11223344, reply=True).to_bytes()) == b""
    assert responder.peer_features == 0x11223344
