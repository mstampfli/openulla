"""A sink that answers like the earbud does, as far as the earbud's behaviour is proven.

This is what lets a source be exercised without the hardware. It models three things and
nothing more:

  the AAP service-4 surface   open, the commands of section 2.4, the status reply
  the session manager         the firmware's own transition table, through openulla.session
  the controller              the link-manager exchange, through openulla.controller

What it deliberately does not do is invent timing or internals. A real earbud produces the
internal events (its DSP, its sibling, its controller) itself; here they are fed in with
`feed`, so a test states the order it wants and nothing is assumed. The audio path does not
exist in this simulator at all: section 7 of docs/protocol.md says why the air format is not
recoverable from the firmware.

Proven behaviours it does implement:

  the session belongs to the channel it arrives on, up to three at once
                              [fw rkof 0x6ca6c, 0x4075a]
  accessory version 1.3 in the open reply, status 0
                              [measured against real earbuds]
  a start command is taken only while the manager is idle, otherwise ignored in silence
                              [fw rkof 0x6d602, 0x27a58]
  if the sibling transport is not in an active mode, the answer is status 0x1776 and the
  manager stays idle         [fw rkof 0x274f0]
  status 0 is sent once it reaches the state that waits for the local audio path
                              [fw rkof 0x271f8, which runs in that state]
  the wrapper with class 0x00200000 subscribes the host, tells the sibling and tells the
  local controller            [fw rkof 0x6d116, 0x27f60]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import aap, hci, sibling
from .codes import AapReject, SessionStatus
from .controller import Responder
from .errors import ProtocolError, Refused
from .session import Event, Machine, State

ACCESSORY_VERSION = (1, 3)  # measured
MAX_SESSIONS = 3  # [fw rkof 0x6ca6c]


@dataclass(frozen=True)
class Action:
    """Something the earbud does besides answering the host, with the address that proves it."""

    kind: str  # "sibling", "controller", "report"
    data: bytes
    fw: str


@dataclass
class Session:
    """One service-4 session: the peer it belongs to and the version pair it negotiated."""

    peer: bytes
    version_pair: int
    subscribed: set[int] = field(default_factory=set)


@dataclass
class Sink:
    """One earbud, answering one host."""

    sibling_active: bool = True
    machine: Machine = field(default_factory=lambda: Machine(State.INIT))
    responder: Responder = field(default_factory=Responder)
    sessions: dict[bytes, Session] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    mode: int | None = None

    def __post_init__(self) -> None:
        if self.machine.state == State.INIT:
            self.machine.feed(Event.INIT)

    # -- the host interface ------------------------------------------------------------

    def receive(self, data: bytes, peer: bytes = b"\x00" * 6) -> list[bytes]:
        """Answer one AAP frame from the host. Returns the frames to send back."""
        frame = aap.parse_frame(data)
        if frame.ftype == aap.FrameType.OPEN:
            return [self._open(data, peer)]
        if frame.ftype == aap.FrameType.DATA:
            return self._command(frame, peer)
        return []

    def _open(self, data: bytes, peer: bytes) -> bytes:
        if len(data) < aap.OPEN_REQUEST_LENGTH:
            raise Refused(AapReject.FRAME_TOO_SHORT, "the open request is sixteen bytes")
        if peer in self.sessions:
            raise Refused(AapReject.SESSION_EXISTS, "this channel already has a session")
        if len(self.sessions) >= MAX_SESSIONS:
            raise Refused(AapReject.NO_FREE_SESSION_SLOT, f"only {MAX_SESSIONS} sessions exist")
        frame = aap.parse_frame(data)
        major, minor = int.from_bytes(frame.body[0:2], "little"), int.from_bytes(
            frame.body[2:4], "little"
        )
        self.sessions[peer] = Session(peer=peer, version_pair=(major << 16) | minor)
        return aap.open_reply(*ACCESSORY_VERSION)

    def _command(self, frame: aap.Frame, peer: bytes) -> list[bytes]:
        if peer not in self.sessions:
            # What the firmware does with a command on an unopened service is not established,
            # so this simulator refuses it rather than invent a reply.
            raise ProtocolError("no session on this channel: open service 4 first")
        session = self.sessions[peer]
        opcode, body = aap.parse_payload(frame.body)
        if opcode == aap.Opcode.START_SESSION:
            return self._start_session(body, session)
        if opcode == aap.Opcode.STOP_SESSION:
            self.machine.feed(Event.HOST_SESSION_STOP_REQ)
            return []
        if opcode == aap.Opcode.START_STREAM:
            self.machine.feed(Event.HOST_STREAM_START_REQ)
            return []
        if opcode == aap.Opcode.STOP_STREAM:
            self.machine.feed(Event.HOST_STREAM_STOP_REQ)
            return []
        if opcode == aap.Opcode.CONFIGURE_STATISTICS:
            self.actions.append(
                Action("sibling", sibling.frame(sibling.Message.CONFIGURE_STATISTICS, body),
                       "rkof 0x6d2e0")
            )
            return []
        if opcode == aap.Opcode.WRAPPER:
            return self._wrapper(frame, session)
        raise ProtocolError(f"opcode 0x{opcode:02x} is not one this sink answers")

    def _start_session(self, body: bytes, session: Session) -> list[bytes]:
        if len(body) != 1:
            raise ProtocolError("the start command carries one mode byte")
        if self.machine.state != State.IDLE:
            return []  # ignored in silence [fw rkof 0x6d602, 0x27a58]
        if not self.sibling_active:
            return [aap.status_reply(SessionStatus.SIBLING_TRANSPORT_NOT_READY)]
        self.mode = body[0]
        self.machine.feed(Event.SESSION_START_REQ)
        self.actions.append(
            Action(
                "sibling",
                sibling.start_session_request(session.peer, self.mode),
                "rkof 0x274f0 then the sender at 0x2aee0",
            )
        )
        return []

    def _wrapper(self, frame: aap.Frame, session: Session) -> list[bytes]:
        width = aap.class_width(session.version_pair)
        wrapped = aap.parse_wrapper(frame.body, width)
        if wrapped.cls_ == aap.CLASS_ULLA_REPORTS:
            session.subscribed.add(wrapped.cls_)
            self.actions.append(
                Action("sibling", sibling.empty(sibling.Message.DSP_CONFIG_COMPLETE)[:2],
                       "rkof 0x27f9a, the sibling is told")
            )
            self.actions.append(
                Action("controller", hci.command(hci.vendor_opcode(0x375)),
                       "rkof 0x27fb0, the local controller is told")
            )
            return []
        raise ProtocolError(f"report class 0x{wrapped.cls_:x} is not one this sink answers")

    # -- the internal events a real earbud raises itself -------------------------------

    def feed(self, event: Event) -> list[bytes]:
        """Inject one manager event. Returns anything the host would be sent as a result."""
        before = self.machine.state
        step = self.machine.feed(event)
        replies: list[bytes] = []
        if step.after == State.WAIT_AUDIO_QUIESCE and before != State.WAIT_AUDIO_QUIESCE:
            replies.append(aap.status_reply(SessionStatus.OK))  # [fw rkof 0x271f8]
        return replies

    @property
    def state(self) -> State:
        return self.machine.state

    def close(self, peer: bytes = b"\x00" * 6) -> None:
        """Closing the channel ends the session, and a later open can start another.

        How the firmware gets there is not established. Its table answers the disconnect
        event only in the states before a session is open, and in the states after it the
        link-down event is what tears things down (docs/protocol.md 3.4), so this feeds the
        disconnect where there is a row for it and otherwise returns the machine to idle
        rather than inventing an event.
        """
        self.sessions.pop(peer, None)
        self.machine.feed(Event.DISCONNECT)
        if self.machine.state not in (State.INIT, State.IDLE):
            self.machine.state = State.IDLE
