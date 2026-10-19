"""A whole negotiation with no hardware: a source, a simulated earbud, and the two controllers.

This exists so an implementation can be exercised and read end to end. It makes no claim
about timing or about anything the firmware does internally: the internal events are fed in
explicitly, in the order docs/protocol.md section 3.4 gives for them, and the audio layer is
absent because section 7 says why it cannot be recovered from the firmware.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import aap, procedure
from .controller import Initiator, Responder
from .errors import ProtocolError
from .hci import ModeChangeEvent, UllaSetup
from .procedure import SinkState
from .session import Event, State
from .sink import Sink
from .source import Source

DEFAULT_PEER = bytes.fromhex("112233445566")  # a host address, in the order the stack holds it


class SinkTransport:
    """A Transport that hands each frame to a Sink and queues whatever comes back."""

    def __init__(self, sink: Sink, peer: bytes = DEFAULT_PEER) -> None:
        self.sink = sink
        self.peer = peer
        self.inbox: deque[bytes] = deque()
        self.sent: list[bytes] = []
        self.received: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        for reply in self.sink.receive(data, self.peer):
            self.deliver(reply)

    def deliver(self, data: bytes) -> None:
        self.inbox.append(bytes(data))

    def recv(self, timeout: float | None = None) -> bytes:
        if not self.inbox:
            raise TimeoutError("the simulated earbud has sent nothing")
        data = self.inbox.popleft()
        self.received.append(data)
        return data

    def pending(self) -> int:
        return len(self.inbox)

    def close(self) -> None:
        self.sink.close(self.peer)


@dataclass
class Link:
    """One simulated setup: the host side, the earbud side, and the two controllers."""

    sink: Sink = field(default_factory=Sink)
    peer: bytes = DEFAULT_PEER
    initiator: Initiator = field(default_factory=lambda: Initiator(radio_value=0))
    transport: SinkTransport = field(init=False)
    source: Source = field(init=False)
    pdus: list[tuple[str, bytes]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transport = SinkTransport(self.sink, self.peer)
        self.source = Source(self.transport)

    # -- the earbud's own events --------------------------------------------------------

    def feed(self, event: Event) -> None:
        """Raise one manager event inside the earbud, delivering anything the host is sent."""
        for frame in self.sink.feed(event):
            self.transport.deliver(frame)

    def feed_all(self, *events: Event) -> None:
        for event in events:
            self.feed(event)

    # -- the controller-to-controller exchange ------------------------------------------

    def exchange(self, pdu: bytes, responder: Responder | None = None) -> bytes:
        """Send one link-manager PDU to the peer controller and record both directions."""
        peer = responder or self.sink.responder
        self.pdus.append(("source -> sink", pdu))
        reply = peer.receive(pdu)
        if reply:
            self.pdus.append(("sink -> source", reply))
        return reply

    def controller_setup(
        self, setup: UllaSetup, responder: Responder | None = None
    ) -> ModeChangeEvent:
        """Run the link-manager exchange and return the event the host would see.

        On success the earbud's manager is given the link-up event, which is what the real
        controller reports to it [fw rkof event 29 in docs/protocol.md 3.3].
        """
        peer = responder or self.sink.responder
        request = self.initiator.setup_request(setup)
        self.pdus.append(("source -> sink", request))
        reply = peer.receive(request)
        if not reply:
            raise ProtocolError("the sink held the answer back; its sibling link is not ready")
        self.pdus.append(("sink -> source", reply))
        answer = self.initiator.on_reply(reply)
        if answer.host_event is not None:
            return answer.host_event  # the peer refused, and that is the whole exchange
        if answer.pdu:
            self.pdus.append(("source -> sink", answer.pdu))
            # Nothing answers a configuration PDU on the air: the receiver hands its two
            # values to the procedure and sends nothing back (5.2), so a reply here would
            # mean the responder had invented one.
            if peer.receive(answer.pdu):
                raise ProtocolError("a configuration PDU is not answered on the air")
        # What follows is the step the earbud's own firmware cannot take: nothing in it posts
        # the event its procedure's third state waits for (protocol.md 5.9), so a source has
        # to drive it. The simulator does, and says so rather than pretending otherwise.
        if peer.procedure.state is SinkState.AWAIT_MODULE_RESULT:
            peer.procedure.feed(procedure.Event.MODULE_RESULT)  # the sink reports and idles
        event = self.initiator.on_module_result()
        if event.result == 0:
            self.feed(Event.ULLA_LINK_UP)
        return event

    # -- convenience for a whole run ----------------------------------------------------

    def open_and_start(self, mode: int = 0) -> int:
        """Open the service, ask for a session, and return the status the earbud answers."""
        self.source.open_service(timeout=None)
        self.source.start_session(mode)
        self.feed_all(Event.APP_SETUP_COMPLETE, Event.SUPERVISION_TIMEOUT_UPDATED)
        self.feed(Event.PEER_SESSION_START_RSP)
        return self.source.read_status(timeout=None)

    def quiesce(self) -> None:
        """The local audio path has stopped, which is what moves it to controller setup."""
        self.feed(Event.LOCAL_FLOW_OFF_DONE)

    def controller_teardown(self, responder: Responder | None = None) -> ModeChangeEvent:
        """Run the second half of both procedures: the teardown. [protocol.md 5.9, 5.10]

        Both ends start it at their own fourth state, the source's through the entry that
        puts the link in mode 0x36 and the sink's through the matching one. The events after
        that are the ones their handlers expect: the teardown's accepted answer, then the
        callbacks each registers, and the last one reports mode 0 with the success result.
        """
        peer = responder or self.sink.responder
        source, sink = self.initiator.procedure, peer.procedure
        source.begin_teardown()
        sink.begin_teardown()
        # The source's fifth state wants three flags: the two paths of the teardown's
        # accepted answer and its own link condition (5.9).
        source.feed(procedure.Event.LINK_STATE_ADVANCED)
        source.path_reported(both=True)  # posts event 6, the teardown's accepted answer
        sink.feed(procedure.Event.TEARDOWN_ACCEPTED)
        source.feed(procedure.Event.CALLBACK_A)
        sink.feed(procedure.Event.CALLBACK_A)
        sink.feed(procedure.Event.CALLBACK_B)
        step = source.feed(procedure.Event.TEARDOWN_COMPLETE)
        if step.host_event is None:
            raise ProtocolError("the teardown did not report to the host")
        return step.host_event

    def finish_setup(self) -> State:
        """The DSP's side of the session, after the link is up."""
        self.feed_all(
            Event.DSP_SESSION_READY, Event.PEER_DSP_CONFIG_COMPLETE, Event.DSP_SETUP_COMPLETE
        )
        return self.sink.state

    def transcript(self) -> list[str]:
        """Every frame and PDU of the run, in order, as hex."""
        lines = []
        for data in self.transport.sent:
            lines.append(f"host -> bud  {_describe(data)}")
        for direction, pdu in self.pdus:
            lines.append(f"{direction}  LMP {pdu.hex(' ')}")
        return lines


def _describe(frame: bytes) -> str:
    parsed = aap.parse_frame(frame)
    head = f"type {parsed.ftype} service {parsed.service}"
    if parsed.ftype == aap.FrameType.DATA:
        opcode, body = aap.parse_payload(parsed.body)
        return f"{head} opcode 0x{opcode:02x} body {body.hex(' ') or '-'}"
    return f"{head} body {parsed.body.hex(' ')}"


def connect(**kwargs: object) -> Link:
    """A link whose earbud is in the state a real one is in before a session starts."""
    return Link(**kwargs)  # type: ignore[arg-type]
