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

Reasons 1 to 6 are what this receiver puts on the air. The 0x81 to 0x87 names belong to the
primary role's own failures in the host-side name table [fw rkof 0x2777c] and are not emitted
here; where the 0x80 bit is set was not established.

On acceptance the sink copies the parsed fields into its own context, forwards an identical
0x73 to its sibling link, and sends the accepted PDU only once both links are ready
[fw acib 0x677c8, 0x303a8, 0x302f8].
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import lmp
from .codes import Mode, RejectReason, Result
from .errors import ProtocolError
from .hci import ModeChangeEvent, UllaSetup

ACCEPT = 0


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
        """0 to accept, otherwise the reason byte. [fw acib 0x675f4]"""
        if not self.feature_enabled:
            return RejectReason.SECONDARY_ROLE_NOT_INITIATED
        if not self.object_ready:
            return 2  # the slot the host-side name table leaves unnamed
        if self.busy:
            return RejectReason.SECONDARY_DISALLOWED_ACTIVITY
        if self.link_state not in (0, 4, 5, 6):
            raise ProtocolError("the link-state check returns 0, 4, 5 or 6")
        return self.link_state


@dataclass
class Responder:
    """The sink's controller: answers a setup request, forwarding to its sibling first."""

    checks: AcceptChecks = field(default_factory=AcceptChecks)
    sibling_ready: bool = True
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
            return b""  # stored without checks [fw acib 0x6352c]
        if isinstance(parsed, lmp.AllowedBands):
            return b""  # applied through the intersection rule, see bands.link_mask
        if isinstance(parsed, lmp.BandRanges):
            for r in parsed.ranges:
                self.band_ranges[r.band] = (r.first_channel, r.length)
            return b""
        raise ProtocolError("this responder answers the setup PDUs only")

    def _setup(self, request: lmp.SetupRequest) -> bytes:
        reason = self.checks.decide()
        if reason != ACCEPT:
            return lmp.NotAccepted(lmp.OPCODE_ULLA_SETUP, reason, tid=request.tid).to_bytes()
        self.received = request
        self.forwarded = request.to_bytes()  # the identical PDU the sink sends on
        if not self.sibling_ready:
            return b""  # the accepted PDU waits until both links are ready
        return lmp.Accepted(lmp.OPCODE_ULLA_SETUP, tid=request.tid).to_bytes()

    def sibling_became_ready(self, tid: int = 0) -> bytes:
        """The accepted PDU the sink held back until its second link was ready."""
        if self.received is None:
            raise ProtocolError("no setup request was accepted")
        self.sibling_ready = True
        return lmp.Accepted(lmp.OPCODE_ULLA_SETUP, tid=tid).to_bytes()


@dataclass
class Initiator:
    """The source's controller: emits the setup request and reports the outcome to its host.

    A real controller reads the four-byte radio value from its own hardware with a validity
    check and up to ten retries [fw acib 0xdc9c0]; there is nothing to read here, so the
    caller supplies it and the simulated value is whatever it passes.
    """

    handle: int = 0
    radio_value: int = 0
    tid: int = 0

    def setup_request(self, setup: UllaSetup) -> bytes:
        """Build the 0x73 PDU from a host's 0xFE50 parameters. [fw acib 0x1da40, 0x3aa6e]

        The configuration word is passed through, the flag byte carries bits 0, 1 and 3 of
        the command's flags, and the command's third byte becomes the last PDU byte.
        """
        self.handle = setup.handle
        return lmp.SetupRequest(
            radio_value=self.radio_value,
            config_word=setup.config_word,
            flags=setup.flags & 0b1011,
            extra_flags=setup.extra_flags,
            tid=self.tid,
        ).to_bytes()

    def on_reply(self, pdu: bytes) -> ModeChangeEvent:
        """Turn the peer's answer into the vendor event the host sees. [fw acib 0x11d34]"""
        parsed = lmp.parse(pdu)
        if isinstance(parsed, lmp.Accepted):
            if parsed.accepted_opcode != lmp.OPCODE_ULLA_SETUP:
                raise ProtocolError("the accepted PDU names another opcode")
            return ModeChangeEvent(
                handle=self.handle,
                mode=Mode.ULLA_ON,
                result=Result.SUCCESS,
                reason=RejectReason.NOT_APPLICABLE,
            )
        if isinstance(parsed, lmp.NotAccepted):
            if parsed.rejected_opcode != lmp.OPCODE_ULLA_SETUP:
                raise ProtocolError("the not-accepted PDU names another opcode")
            return ModeChangeEvent(
                handle=self.handle,
                mode=Mode.BT_ACL_ACTIVE,
                result=Result.SETUP_REJECTED_BY_PEER,
                reason=parsed.reason,
            )
        raise ProtocolError("a setup request is answered with accepted or not-accepted")
