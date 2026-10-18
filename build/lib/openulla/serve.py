"""Answer an accessory-protocol channel the way the earbud does, frame by frame.

openulla.sink holds what the earbud answers; this wraps it for something that reads frames
off a channel, and optionally supplies the internal events a real pair raises by itself so
that a source talking to it gets as far as it would with real earbuds.

Those events are the simulator's doing and they are marked as such: a real earbud raises them
when its app, its sibling and its DSP get there, and nothing here models that timing. The
order is the one docs/protocol.md 3.4 gives.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import aap
from .errors import ProtocolError, Refused
from .session import Event, State
from .sink import Sink

# What a real pair raises on its own once the host has asked, in the table's order. The first
# three are the earbud's own; the last five stand in for a controller and a DSP that are not
# here, and each one is logged by name as it is fed so that nothing passes for real.
AFTER_START = (
    Event.APP_SETUP_COMPLETE,
    Event.SUPERVISION_TIMEOUT_UPDATED,
    Event.PEER_SESSION_START_RSP,
    Event.LOCAL_FLOW_OFF_DONE,
    Event.ULLA_LINK_UP,
    Event.DSP_SESSION_READY,
    Event.PEER_DSP_CONFIG_COMPLETE,
    Event.DSP_SETUP_COMPLETE,
)
AFTER_STREAM_START = (
    Event.STREAM_READY_FROM_DSP,
    Event.STREAM_READY_FROM_CONTROLLER,
    Event.PEER_STREAM_START_RSP,
    Event.STREAM_HOST_SETUP_COMPLETE,
)
AFTER_STREAM_STOP = (Event.PEER_STREAM_STOP_RSP,)
AFTER_SESSION_STOP = (Event.PEER_SESSION_STOP_RSP,)


@dataclass
class Service:
    """One channel's worth of earbud behaviour."""

    sink: Sink = field(default_factory=Sink)
    peer: bytes = b"\x00" * 6
    advance: bool = True
    log: Callable[[str], None] = lambda line: None

    def handle(self, data: bytes) -> list[bytes]:
        """Answer one inbound frame with the frames to send back."""
        try:
            replies = list(self.sink.receive(data, self.peer))
        except Refused as refused:
            self.log(f"refused with 0x{refused.code:02x}: {refused}")
            return []
        except ProtocolError as exc:
            self.log(f"not a frame this sink answers: {exc}")
            return []
        if self.advance:
            replies += self._advance(data)
        return replies

    def _advance(self, data: bytes) -> list[bytes]:
        """Feed the events a real pair would raise after this frame."""
        frame = aap.parse_frame(data)
        if frame.ftype != aap.FrameType.DATA or len(frame.body) < 2:
            return []
        opcode, _body = aap.parse_payload(frame.body)
        events: tuple[Event, ...] = ()
        if opcode == aap.Opcode.START_SESSION and self.sink.state == State.HOST_SETUP:
            events = AFTER_START
        elif opcode == aap.Opcode.START_STREAM and self.sink.state == State.STREAM_HOST_SETUP:
            events = AFTER_STREAM_START
        elif opcode == aap.Opcode.STOP_STREAM and self.sink.state == State.WAIT_AUDIO_STOPPED:
            events = AFTER_STREAM_STOP
        elif opcode == aap.Opcode.STOP_SESSION and self.sink.state == State.WAIT_STOP_RSP:
            events = AFTER_SESSION_STOP
        out: list[bytes] = []
        for event in events:
            out += self.sink.feed(event)
            self.log(f"    simulated event {event.name}, now in {self.sink.state.name}")
        return out

    def closed(self) -> None:
        """The channel went away, which resets the earbud's session state [measured]."""
        self.sink.close(self.peer)
