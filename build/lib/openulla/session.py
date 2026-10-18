"""The earbud's session manager: the table-driven machine that runs a ULLA session.

For the current state the firmware searches that state's rows for the incoming event, calls
the row's handler, and takes one of two next states depending on whether the handler returned
0x1770. A next state of 0x10 means stay. An event with no row in the current state is ignored
and the state does not change. [fw rkof 0x25f1c]

The state and event numbers are the firmware's own: the dispatcher indexes its name tables by
the numeric value [fw rkof 0x25f26, 0x25f34]. The index table of the rows is at
rkof 0x1be53c and the rows themselves run from rkof 0x1be23c; every handler address in the
table below is the one that row names.

Nothing models what a handler does internally. Two behaviours are proven and used by
openulla.sink: on event 3 in idle the manager answers 0x1776 and stays idle unless the
sibling transport is in an active mode [fw rkof 0x274f0], and the status reply with 0 is sent
when it reaches the state that waits for the local audio path [fw rkof 0x271f8].
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import IntEnum

HANDLER_OK = 0x1770


class State(IntEnum):
    INIT = 0
    IDLE = 1
    HOST_SETUP = 2
    WAIT_SESSION_RSP = 3
    WAIT_AUDIO_QUIESCE = 4
    CONTROLLER_SETUP = 5
    DSP_SETUP = 6
    SESSION_OPEN = 7
    STREAM_HOST_SETUP = 8
    STREAM_OPEN = 9
    WAIT_AUDIO_STOPPED = 10
    STREAM_HOST_TEARDOWN = 11
    WAIT_STOP_RSP = 12
    CONTROLLER_TEARDOWN = 13
    HOST_TEARDOWN = 14
    WAIT_ABORT_RSP = 15
    NO_CHANGE = 16


STAY = State.NO_CHANGE


class Event(IntEnum):
    UNKNOWN = 0
    INIT = 1
    RESET = 2
    SESSION_START_REQ = 3
    HOST_SESSION_STOP_REQ = 4
    PEER_SESSION_STOP_REQ = 5
    PEER_SESSION_START_RSP = 6
    PEER_SESSION_STOP_RSP = 7
    DSP_SESSION_READY = 8
    SIBLING_AUDIO_CONFIGURED = 9
    SIBLING_AUDIO_DECONFIGURED = 10
    PEER_DSP_CONFIG_COMPLETE = 11
    APP_SETUP_COMPLETE = 12
    DSP_SETUP_COMPLETE = 13
    PEER_SESSION_STOPPED = 14
    HOST_STREAM_START_REQ = 15
    HOST_STREAM_STOP_REQ = 16
    PEER_STREAM_START_REQ = 17
    PEER_STREAM_STOP_REQ = 18
    PEER_STREAM_START_RSP = 19
    PEER_STREAM_STOP_RSP = 20
    STREAM_READY_FROM_CONTROLLER = 21
    STREAM_STOP_RSP_FROM_CONTROLLER = 22
    STREAM_START_RSP_FROM_DSP = 23
    STREAM_STOP_RSP_FROM_DSP = 24
    STREAM_READY_FROM_DSP = 25
    STREAM_HOST_SETUP_COMPLETE = 26
    SUPERVISION_TIMEOUT_UPDATED = 27
    UNSNIFF_COMPLETED = 28
    ULLA_LINK_UP = 29
    ULLA_LINK_DOWN = 30
    GOT_DATA_PATH_CONFIGS = 31
    SNOOP_MODE_UPDATE = 32
    SNOOP_MODE_ENABLED = 33
    SNOOP_MODE_DISABLED = 34
    LOCAL_FLOW_OFF_DONE = 35
    PEER_ABORT_REQ = 36
    PEER_ABORT_RSP = 37
    DISCONNECT = 38
    ABORT = 39


@dataclass(frozen=True)
class Transition:
    """One row: the events it answers, the two next states, and the handler it names."""

    state: State
    events: tuple[Event, ...]
    ok: State
    other: State
    handler: int

    def next_state(self, current: State, returned_ok: bool) -> State:
        target = self.ok if returned_ok else self.other
        return current if target == State.NO_CHANGE else target


def _row(
    state: State, events: Iterable[Event], ok: State, other: State, handler: int
) -> Transition:
    return Transition(state, tuple(events), ok, other, handler)


S, E = State, Event

# Every row of the firmware's table, in the firmware's order. The handler column is the
# address the row names, kept so a reader can go back to the image.
TRANSITIONS: tuple[Transition, ...] = (
    _row(S.INIT, (E.INIT,), S.IDLE, S.IDLE, 0x27620),
    _row(S.IDLE, (E.SESSION_START_REQ,), S.HOST_SETUP, STAY, 0x274F0),
    _row(S.IDLE, (E.RESET,), STAY, STAY, 0x275D4),
    _row(S.HOST_SETUP, (E.APP_SETUP_COMPLETE,), STAY, S.WAIT_AUDIO_QUIESCE, 0x27374),
    _row(S.HOST_SETUP, (E.SIBLING_AUDIO_CONFIGURED,), STAY, STAY, 0x273D4),
    _row(
        S.HOST_SETUP,
        (E.SUPERVISION_TIMEOUT_UPDATED,),
        S.WAIT_SESSION_RSP,
        S.WAIT_AUDIO_QUIESCE,
        0x27438,
    ),
    _row(S.HOST_SETUP, (E.DISCONNECT, E.ABORT), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.WAIT_SESSION_RSP, (E.PEER_SESSION_START_RSP,), S.WAIT_AUDIO_QUIESCE, STAY, 0x27330),
    _row(S.WAIT_SESSION_RSP, (E.DISCONNECT, E.ABORT), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(
        S.WAIT_AUDIO_QUIESCE,
        (E.LOCAL_FLOW_OFF_DONE,),
        S.CONTROLLER_SETUP,
        S.CONTROLLER_TEARDOWN,
        0x27198,
    ),
    _row(S.WAIT_AUDIO_QUIESCE, (E.DISCONNECT, E.ABORT), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.CONTROLLER_SETUP, (E.ULLA_LINK_UP,), S.DSP_SETUP, STAY, 0x270B4),
    _row(S.CONTROLLER_SETUP, (E.ULLA_LINK_DOWN, E.DISCONNECT), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.DSP_SETUP, (E.DSP_SESSION_READY,), STAY, STAY, 0x26DC4),
    _row(S.DSP_SETUP, (E.PEER_DSP_CONFIG_COMPLETE,), STAY, STAY, 0x26E48),
    _row(S.DSP_SETUP, (E.DSP_SETUP_COMPLETE,), S.SESSION_OPEN, STAY, 0x26EAC),
    _row(S.DSP_SETUP, (E.ULLA_LINK_DOWN, E.DISCONNECT), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(
        S.SESSION_OPEN,
        (E.HOST_STREAM_START_REQ, E.PEER_STREAM_START_REQ),
        S.STREAM_HOST_SETUP,
        STAY,
        0x26C78,
    ),
    _row(
        S.SESSION_OPEN,
        (E.PEER_SESSION_STOP_REQ,),
        S.WAIT_STOP_RSP,
        S.WAIT_AUDIO_QUIESCE,
        0x26D24,
    ),
    _row(S.SESSION_OPEN, (E.HOST_SESSION_STOP_REQ,), S.WAIT_STOP_RSP, STAY, 0x26D24),
    _row(S.SESSION_OPEN, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.STREAM_HOST_SETUP, (E.STREAM_READY_FROM_DSP,), STAY, STAY, 0x268CC),
    _row(S.STREAM_HOST_SETUP, (E.STREAM_READY_FROM_CONTROLLER,), STAY, STAY, 0x26974),
    _row(S.STREAM_HOST_SETUP, (E.PEER_STREAM_START_RSP,), STAY, STAY, 0x26A1C),
    _row(S.STREAM_HOST_SETUP, (E.STREAM_HOST_SETUP_COMPLETE,), S.STREAM_OPEN, STAY, 0x26A88),
    _row(S.STREAM_HOST_SETUP, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(
        S.STREAM_OPEN,
        (E.HOST_SESSION_STOP_REQ, E.HOST_STREAM_STOP_REQ),
        S.WAIT_AUDIO_STOPPED,
        STAY,
        0x26828,
    ),
    _row(S.STREAM_OPEN, (E.PEER_STREAM_STOP_REQ,), S.STREAM_HOST_TEARDOWN, STAY, 0x2682C),
    _row(S.STREAM_OPEN, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.WAIT_AUDIO_STOPPED, (E.PEER_STREAM_STOP_RSP,), S.SESSION_OPEN, STAY, 0x26824),
    _row(S.WAIT_AUDIO_STOPPED, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(
        S.STREAM_HOST_TEARDOWN,
        (E.STREAM_STOP_RSP_FROM_CONTROLLER,),
        STAY,
        STAY,
        0x26560,
    ),
    _row(S.STREAM_HOST_TEARDOWN, (E.STREAM_STOP_RSP_FROM_DSP,), S.SESSION_OPEN, STAY, 0x2660C),
    _row(S.STREAM_HOST_TEARDOWN, (E.PEER_STREAM_STOP_RSP,), S.SESSION_OPEN, STAY, 0x2671C),
    _row(S.STREAM_HOST_TEARDOWN, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.WAIT_STOP_RSP, (E.PEER_SESSION_STOP_RSP,), S.WAIT_AUDIO_QUIESCE, STAY, 0x264B0),
    _row(S.WAIT_STOP_RSP, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.CONTROLLER_TEARDOWN, (E.ULLA_LINK_DOWN,), S.IDLE, S.HOST_TEARDOWN, 0x26308),
    _row(S.HOST_TEARDOWN, (E.SUPERVISION_TIMEOUT_UPDATED,), S.IDLE, STAY, 0x260C8),
    _row(S.HOST_TEARDOWN, (E.PEER_SESSION_STOPPED,), S.IDLE, STAY, 0x260E0),
    _row(S.HOST_TEARDOWN, (E.DISCONNECT,), S.IDLE, S.IDLE, 0x260E4),
    # State 15, waiting for an abort response, has no rows in this build.
)


def row_for(state: State, event: Event) -> Transition | None:
    """The row the firmware would find, or None when the event is ignored."""
    for row in TRANSITIONS:
        if row.state == state and event in row.events:
            return row
    return None


@dataclass(frozen=True)
class Step:
    """What one event did: the row it found, what the handler returned, and the two states."""

    event: Event
    before: State
    after: State
    row: Transition | None
    returned: int | None

    @property
    def handled(self) -> bool:
        return self.row is not None

    @property
    def changed(self) -> bool:
        return self.before != self.after


Handler = Callable[["Machine", Event], int]


@dataclass
class Machine:
    """The manager's own machine, driven one event at a time.

    A handler registered for (state, event) is called and its return value decides which of
    the row's two next states is taken; without one the row's ok branch is taken, which is
    what 0x1770 means.
    """

    state: State = State.INIT
    handlers: dict[tuple[State, Event], Handler] = field(default_factory=dict)
    history: list[Step] = field(default_factory=list)

    def on(self, state: State, event: Event, handler: Handler) -> None:
        self.handlers[(state, event)] = handler

    def feed(self, event: Event) -> Step:
        before = self.state
        row = row_for(before, event)
        if row is None:
            step = Step(event, before, before, None, None)
            self.history.append(step)
            return step
        handler = self.handlers.get((before, event))
        returned = handler(self, event) if handler is not None else HANDLER_OK
        self.state = row.next_state(before, returned == HANDLER_OK)
        step = Step(event, before, self.state, row, returned)
        self.history.append(step)
        return step

    def feed_all(self, events: Iterable[Event]) -> list[Step]:
        return [self.feed(e) for e in events]
