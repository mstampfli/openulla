"""The controller side: what each end does with the link-manager PDUs.

The responder's decision is four checks in a fixed order, each with its own reason, read from
the receiver itself [fw acib 0x675f4]:

  1  a feature-enabled byte      [fw acib 0x62074]  fails -> reason 1
  2  an object-ready call        [fw acib 0x62030]  fails -> reason 2
  3  a busy flag                 [fw acib 0x62154]  set   -> reason 3
  4  link state, role and count  [fw acib 0x620c8]  returns the reason in its low nibble:
                                 0 to pass, 6 when the link is absent or its state byte is not
                                 0xC or its flag at +0x185 is set, otherwise 4 or 5 from the
                                 role and connection-count tests at 0x62102 onwards

None of them is cryptographic: no key, no nonce, no challenge, and the parser's per-opcode
attribute table imposes no state requirement on 0x73 at all [fw acib 0xf1f58, 0x3b0aa].

The reason that goes on the air is 0x81 to 0x86, not the bare nibble: the receiver sets bit 7
of its answer byte before any check runs and the caller sends the whole byte [fw acib 0x67608,
0x63492 onwards]. Those are the primary-role names in the host-side table [fw rkof 0x2777c],
and the bit says which role is refusing; an earbud facing a source is the primary one.

On acceptance the sink copies the parsed fields into its own context, forwards an identical
0x73 to its sibling link, and sends the accepted PDU only once both links are ready
[fw acib 0x677c8, 0x303a8, 0x302f8].

Two refusals sit outside that list of four, both proven and both published in protocol.md
5.10. Before the checks run, a role test of its own refuses with 0x81 [fw acib 0x634ce]. And
after they pass, a request the sink cannot hand to its sibling is refused with 0x24, the
standard "LMP PDU not allowed" code, which the firmware's own reason formatter cannot even
name [fw acib 0x6783c]. So a decoder has to pass an unknown reason byte through.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import lmp
from .errors import ProtocolError
from .hci import ModeChangeEvent, UllaSetup
from .procedure import (
    MODULE_RESULT_OK,
    Action,
    Event,
    Procedure,
    SinkProcedure,
    SinkState,
    State,
)

ACCEPT = 0
PRIMARY_ROLE = 0x80  # bit 7 of the reason byte: the role of the device refusing


@dataclass
class AcceptChecks:
    """The four runtime conditions the responder tests, in the firmware's own order.

    Every field is per-link or per-device state a real controller reads from its own
    registers and objects, so an implementation supplies them; the defaults are the state in
    which a setup request is accepted.
    """

    feature_enabled: bool = True
    object_ready: bool = True
    busy: bool = False
    link_state: int = 0

    def decide(self) -> int:
        """0 to accept, otherwise the reason byte that goes on the air. [fw acib 0x675f4]

        The receiver sets bit 7 of its answer byte before any check runs [fw acib 0x67608]
        and writes the failing check's number into the low nibble, and the caller sends the
        whole byte [fw acib 0x63492 onwards]. So the reasons an earbud refuses with are the
        primary-role half of the list, 0x81 to 0x86.
        """
        if not self.feature_enabled:
            return PRIMARY_ROLE | 1
        if not self.object_ready:
            return PRIMARY_ROLE | 2
        if self.busy:
            return PRIMARY_ROLE | 3
        if self.link_state not in (0, 4, 5, 6):
            raise ProtocolError("the link-state check returns 0, 4, 5 or 6")
        return PRIMARY_ROLE | self.link_state if self.link_state else ACCEPT


@dataclass
class Responder:
    """The sink's controller: answers a setup request, forwarding to its sibling first.

    The decisions about *when* each PDU goes out are not made here: they belong to the sink's
    own procedure, which openulla.procedure holds as the firmware's eight-state table, and
    this class drives that one instead of keeping a second copy of the same rules
    [fw acib 0x63448 for this order, 0x677c8 and 0x67858 for the states].
    """

    checks: AcceptChecks = field(default_factory=AcceptChecks)
    sibling_ready: bool = True
    """Whether the second link is ready: the procedure's own both-links test."""
    procedure: SinkProcedure = field(default_factory=SinkProcedure)
    received: lmp.SetupRequest | None = None
    forwarded: bytes | None = None
    link_band: int | None = None
    band_ranges: dict[int, tuple[int, int]] = field(default_factory=dict)
    features: int = 0  # what this end advertises
    peer_features: int | None = None  # what the other end sent, kept per link

    def receive(self, pdu: bytes) -> bytes:
        """Answer one PDU: the accepted or not-accepted reply, or b"" when there is none."""
        parsed = lmp.parse(pdu)
        if isinstance(parsed, lmp.SetupRequest):
            return self._setup(parsed)
        if isinstance(parsed, lmp.Features):
            # The peer's word is kept against the link, and a request is answered with ours
            # [fw acib 0x3c0a8 then 0x29470].
            self.peer_features = parsed.word
            if parsed.reply:
                return b""
            return lmp.Features(self.features, reply=True, tid=parsed.tid).to_bytes()
        if isinstance(parsed, lmp.Configuration):
            # No checks, and no answer on the air: what a received 0x74 does is post event 3
            # to this procedure, which is the step that builds the request block
            # [fw acib 0x6352c into the slot at +0xC8, protocol.md 5.2 and 5.9].
            if self.procedure.state is SinkState.AWAIT_MODULE_CALL:
                self.procedure.feed(Event.MODULE_CALL)
            return b""
        if isinstance(parsed, lmp.AllowedBands):
            return b""  # applied through the intersection rule, see bands.link_mask
        if isinstance(parsed, lmp.BandRanges):
            for r in parsed.ranges:
                self.band_ranges[r.band] = (r.first_channel, r.length)
            return b""
        raise ProtocolError("this responder answers the setup PDUs only")

    def _setup(self, request: lmp.SetupRequest) -> bytes:
        """The four checks, then the procedure's first two states. [fw acib 0x63448]"""
        reason = self.checks.decide()
        if reason != ACCEPT:
            return lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, reason, tid=request.tid).to_bytes()
        self.procedure.both_links_ready = self.sibling_ready
        step = self.procedure.begin()
        if step.action is Action.REFUSE:
            # Not one of the reasons the checks give: the sibling could not take it (5.10).
            return lmp.NotAccepted(
                lmp.OPCODE_ULLA_SETUP, step.reason or 0, tid=request.tid
            ).to_bytes()
        self.received = request
        if step.action is Action.FORWARD_TO_SIBLING:
            self.forwarded = request.to_bytes()  # the identical PDU the sink sends on
        return self._accept_when_both_links_are(Event.SETUP_ACCEPTED, request.tid)

    def _accept_when_both_links_are(self, event: Event, tid: int) -> bytes:
        """Feed the procedure and send the accepted PDU only if it says to. [fw acib 0x67858]"""
        if self.procedure.state is not SinkState.AWAIT_BOTH_LINKS:
            return lmp.Accepted(lmp.OPCODE_ULLA_SETUP, tid=tid).to_bytes()
        step = self.procedure.feed(event)
        if step.action is not Action.SEND_ACCEPTED:
            return b""  # the accepted PDU waits until both links are ready
        return lmp.Accepted(lmp.OPCODE_ULLA_SETUP, tid=tid).to_bytes()

    def sibling_became_ready(self, tid: int = 0) -> bytes:
        """The accepted PDU the sink held back until its second link was ready."""
        if self.received is None:
            raise ProtocolError("no setup request was accepted")
        self.sibling_ready = True
        self.procedure.both_links_ready = True
        return self._accept_when_both_links_are(Event.LINK_STATE_ADVANCED, tid)


@dataclass(frozen=True)
class Reply:
    """What the source does with the peer's answer: send something, tell the host, or both."""

    pdu: bytes = b""
    host_event: ModeChangeEvent | None = None


@dataclass
class Initiator:
    """The source's controller: emits the setup request and reports the outcome to its host.

    A real controller reads the four-byte radio value from its own hardware with a validity
    check and up to ten retries [fw acib 0xdc9c0]; there is nothing to read here, so the
    caller supplies it and the simulated value is whatever it passes.

    Like the responder, it owns a procedure and lets it decide the order. That matters most
    on acceptance: the firmware does **not** tell the host the mode is on when the accepted
    PDU arrives. It sends 0x74 and tells the host only two states later, when its own module
    reports [protocol.md 5.9, fw acib 0x685f8 then 0x68788].
    """

    handle: int = 0
    radio_value: int = 0
    tid: int = 0
    procedure: Procedure = field(default_factory=Procedure)

    def setup_request(self, setup: UllaSetup) -> bytes:
        """Build the 0x73 PDU from a host's 0xFE50 parameters. [fw acib 0x1da40, 0x68588]

        The mapping is the one docs/protocol.md 5.1 proves through the transition context:
        the configuration word is passed through, the PDU's byte at offset 7 is bits 0 and 1
        of the command's flag byte, and the byte at offset 8 is its bit 3 as a 0 or a 1. The
        command's parameter 2 becomes the byte the firmware writes past the declared length,
        which does not reach the air, so it is not sent here either. Bit 2 of the flag byte
        reaches the context and nothing else.
        """
        self.handle = setup.handle
        self.procedure.handle = setup.handle
        step = self.procedure.begin()
        if step.action is not Action.SEND_SETUP_REQUEST:
            raise ProtocolError("the procedure did not ask for the setup request")
        return lmp.SetupRequest(
            radio_value=self.radio_value,
            config_word=setup.config_word,
            flags=setup.flags & 0b11,
            extra_flags=(setup.flags >> 3) & 1,
            tid=self.tid,
        ).to_bytes()

    def on_reply(self, pdu: bytes) -> Reply:
        """Feed the peer's answer to the procedure and do what it says. [protocol.md 5.9]

        An accepted PDU is event 1, and what comes of it is the configuration PDU, not a
        host event. A not-accepted one never reaches the procedure at all: it is reported to
        the host as the peer's refusal [fw acib 0x6848c].
        """
        parsed = lmp.parse(pdu)
        if isinstance(parsed, lmp.Accepted):
            if parsed.accepted_opcode != lmp.OPCODE_ULLA_SETUP:
                raise ProtocolError("the accepted PDU names another opcode")
            step = self.procedure.feed(Event.SETUP_ACCEPTED)
            if step.action is not Action.SEND_CONFIGURATION:
                return Reply()  # the link test has not passed yet, so nothing goes out
            return Reply(pdu=lmp.Configuration(0, 0, tid=self.tid).to_bytes())
        if isinstance(parsed, lmp.NotAccepted):
            if parsed.rejected_opcode != lmp.OPCODE_ULLA_SETUP:
                raise ProtocolError("the not-accepted PDU names another opcode")
            return Reply(host_event=self.procedure.peer_refused(parsed.reason))
        raise ProtocolError("a setup request is answered with accepted or not-accepted")

    def on_module_result(self, ok: bool = True) -> ModeChangeEvent:
        """The step a source has to drive itself, and the host event that follows it.

        The procedure's third state waits for an event nothing in the earbud's firmware
        posts, and its fourth turns the module's answer into the host's mode-change event
        (5.9). A source implementation has to supply both, which is what this does: it takes
        the request block as handed down, then reports.
        """
        self.procedure.module_result = MODULE_RESULT_OK if ok else 0
        if self.procedure.state is State.AWAIT_MODULE_CALL:
            self.procedure.feed(Event.MODULE_CALL)
        step = self.procedure.feed(Event.MODULE_RESULT)
        if step.host_event is None:
            raise ProtocolError("the procedure did not report to the host")
        return step.host_event
