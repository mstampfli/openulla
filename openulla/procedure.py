"""The source's setup procedure: the nine-state machine the controller runs per attempt.

This is one layer below openulla.session, which is the earbud's own session manager. The
controller has its own machine for the link-manager exchange, and it is the thing that puts
0x73 and 0x74 on the air and that tells the host how the attempt ended.

How it is driven [fw acib 0x6829c]: the dispatcher takes the procedure object, an event
number and one argument, reads the current state from the object's +0x24, guards against
re-entry with bit 0 of the object's +0x23, calls the handler the table at acib 0xf2664 holds
for that state, and stores the handler's return value back as the new state. There are nine
handlers, so nine states:

    0  acib 0x68588    1  acib 0x685f8    2  acib 0x6869c
    3  acib 0x68788    4  acib 0x687fc    5  acib 0x6886c
    6  acib 0x688f4    7  acib 0x688f8    8  acib 0x68924

The numbers here are the firmware's. The names are ours: unlike the session manager, this
machine has no name tables in the image, so there is nothing to copy. Each name says what
the handler at that state does, nothing more.

An event the current state does not expect is traced and the state is kept, which is why
every handler returns its own number on that path. Only state 6 is different: it returns 0
for any event at all.

"The module" below means the code that owns this procedure, whose block is at acib
0x1000df68: it holds the one procedure object at +0xA4 and its own phase number at +0x84, and
both entry points are called from it. What that module is for beyond driving this exchange is
not traced, so it is not named here.

Where the events come from, each one proven at its posting site:

    0  the source entry point                        [fw acib 0x6842c, called from 0x66da0]
    1  an accepted PDU naming opcode 0x73            [fw acib 0x3c6ec -> 0x61fcc -> 0x684dc]
    2  the link's own state byte read 3 and set to 4  [fw acib 0x628f8 -> 0x68424]
    3  a call inside the module                      [fw acib 0x66db0]
    4  a {byte, word} pair from the module           [fw acib 0x619a4 -> 0x684e4]
    5  the second entry point                        [fw acib 0x68450, called from 0x66d72]
    6  both of the two paths of state 5 reported     [fw acib 0x68578]
    7  the first callback state 5 registers          [fw acib 0x66fe8]
    8  the second callback state 5 registers         [fw acib 0x67028]
    9  the stop entry point                          [fw acib 0x684fc]

A not-accepted PDU naming 0x73 is not an event: it goes straight to the host as mode 0,
result 3 and the peer's own reason byte, and releases the link's procedure slot state 0
claimed [fw acib 0x3c7de -> 0x61ff0 -> 0x6848c]. That is the path `peer_refused` models.

Two limits, stated rather than smoothed over: nothing in the image takes the address of the
function that posts event 3, so what calls it is not traced; and the request block that state
2 builds is modelled by its offsets only, because what the three calls it is handed to do
with it was not followed [fw acib 0x68748 onwards].
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import IntEnum

from .codes import Mode, RejectReason, Result
from .errors import ProtocolError
from .hci import ModeChangeEvent


class State(IntEnum):
    """The nine states, named after what their handler does."""

    SEND_SETUP = 0
    AWAIT_SETUP_ACCEPTED = 1
    AWAIT_MODULE_CALL = 2
    AWAIT_MODULE_RESULT = 3
    AWAIT_SECOND_BEGIN = 4
    AWAIT_THREE_FLAGS = 5
    RESET_ONLY = 6
    AWAIT_CALLBACKS = 7
    ESTABLISHED = 8


class Event(IntEnum):
    """The ten event numbers, named after their posting site."""

    BEGIN = 0
    SETUP_ACCEPTED = 1
    LINK_STATE_ADVANCED = 2
    MODULE_CALL = 3
    MODULE_RESULT = 4
    BEGIN_PHASE_TWO = 5
    BOTH_PATHS_READY = 6
    CALLBACK_A = 7
    CALLBACK_B = 8
    STOP = 9


class Flag(IntEnum):
    """Bits of the object's flag byte at +0x23, which the initialiser clears to 0 to 4.

    Bits 5, 6 and 7 are kept across a run [fw acib 0x6836a masks with 0xE0]; what they mean
    is not traced, so nothing here touches them.
    """

    BUSY = 0x01  # the dispatcher's re-entry guard [fw acib 0x682be, cleared 0x682e6]
    ACCEPTED = 0x02  # set by event 1 in state 1 [fw acib 0x68616]
    PATH_A = 0x04  # set by the two-path callback [fw acib 0x6853e, 0x6855c]
    PATH_B = 0x08  # the same callback, the other path
    LINK = 0x10  # set by event 2 in state 5 [fw acib 0x6888c]


THREE_FLAGS = Flag.PATH_A | Flag.PATH_B | Flag.LINK
MODULE_RESULT_OK = 1  # what the module's check returns on success [fw acib 0x6879e]
DEFAULT_PARAMETERS = (3, 8, 8, 8)
"""The context's +0x14 to +0x17, as the initialiser leaves them [fw acib 0x6832c].

The same four bytes can be set from a caller's buffer through the module's own +0x10C entry
[fw acib 0x68300, installed at 0x6246c], and state 2 copies them into the request block at
its +0x6C and +0x6D to +0x6F [fw acib 0x6871a onwards]. What they mean is not traced.
"""


class Action(IntEnum):
    """What the handler does besides returning a state."""

    NOTHING = 0
    SEND_SETUP_REQUEST = 1  # LMP 0x73 [fw acib 0x685de]
    SEND_CONFIGURATION = 2  # LMP 0x74 [fw acib 0x6866c]
    BUILD_REQUEST_BLOCK = 3  # the 0x74-byte block [fw acib 0x686a6 onwards]
    REGISTER_CALLBACK = 4  # [fw acib 0x6882e]
    REGISTER_CALLBACKS = 5  # both of them [fw acib 0x688c6, 0x688d4]
    TEAR_DOWN = 6  # [fw acib 0x68948 onwards]


@dataclass(frozen=True)
class Step:
    """One event, and everything the firmware does with it."""

    event: Event
    before: State
    after: State
    action: Action = Action.NOTHING
    host_event: ModeChangeEvent | None = None
    expected: bool = True


@dataclass
class Procedure:
    """One attempt. The module keeps a single one of these, at its block's +0xA4.

    So a source runs one setup at a time: the installer writes that pointer and the role
    table in the same breath [fw acib 0x6218c, the object at 0x1000df68 +0xA4].
    """

    handle: int = 0
    state: State = State.SEND_SETUP
    flags: int = 0
    link_test: bool = True
    """What the handler of state 1 asks the link before it sends 0x74 [fw acib 0x68626]."""
    module_result: int = MODULE_RESULT_OK
    """What the module's check returns for the event-4 argument [fw acib 0x6879a]."""
    stop_reason: int = RejectReason.NOT_APPLICABLE
    """The object's +0x1C, which the teardown reports to the host [fw acib 0x6896a]."""
    history: list[Step] = field(default_factory=list)

    # the two entry points ------------------------------------------------------------

    def begin(self) -> Step:
        """Start the first phase: state 0 and event 0 [fw acib 0x6842c]."""
        self.state = State.SEND_SETUP
        self.flags &= ~0x1F
        return self.feed(Event.BEGIN)

    def begin_phase_two(self) -> Step:
        """Start at state 4 with event 5, which is the second entry [fw acib 0x68450]."""
        self.state = State.AWAIT_SECOND_BEGIN
        return self.feed(Event.BEGIN_PHASE_TWO)

    # the dispatcher -----------------------------------------------------------------

    def feed(self, event: Event) -> Step:
        """Run one event through the handler of the current state. [fw acib 0x6829c]"""
        if self.flags & Flag.BUSY:
            raise ProtocolError("the dispatcher refuses to run inside itself")
        self.flags |= Flag.BUSY
        try:
            step = self._handle(event)
        finally:
            self.flags &= ~Flag.BUSY
        self.state = step.after
        self.history.append(step)
        return step

    def feed_all(self, events: Iterable[Event]) -> list[Step]:
        return [self.feed(event) for event in events]

    def _handle(self, event: Event) -> Step:
        before = self.state
        handler = _HANDLERS[before]
        return handler(self, event)

    # what the sites that are not events do --------------------------------------------

    def peer_refused(self, reason: int) -> ModeChangeEvent:
        """The not-accepted PDU for 0x73: report to the host, post no event.

        The state is left as it is, because that path never reaches the dispatcher
        [fw acib 0x6848c]. The reason byte is passed on whole, and its low nibble and bit 7
        are what the firmware's own trace splits out [fw acib 0x684be, 0x684c4].
        """
        if not 0 <= reason <= 0xFF:
            raise ProtocolError("the reason is one byte")
        return ModeChangeEvent(
            handle=self.handle,
            mode=Mode.BT_ACL_ACTIVE,
            result=Result.SETUP_REJECTED_BY_PEER,
            reason=reason,
        )

    def link_lost(self, first_phase: bool) -> ModeChangeEvent:
        """The teardown reporter: which result the host is told depends on the phase.

        The link's own mode byte decides it: 0x35, the first phase, gives "failed to be
        established", anything else gives the supervision-timeout result
        [fw acib 0x68390, the two assignments at 0x683c2 and 0x683fe, the report at 0x6841c].
        """
        return ModeChangeEvent(
            handle=self.handle,
            mode=Mode.BT_ACL_ACTIVE,
            result=Result.FAIL_TO_BE_ESTABLISHED if first_phase else Result.LSTO_ERROR,
            reason=RejectReason.NOT_APPLICABLE,
        )

    def path_reported(self, *, both: bool = False, second: bool = False) -> Step | None:
        """The callback that sets the two path flags and posts event 6 once both are set.

        Its first test decides which it is: when the byte it reads is not zero it sets both
        bits at once and posts straight away, otherwise it sets bit 3 if a second byte reads
        2 and bit 2 if it does not, and posts only once both are set
        [fw acib 0x68536 deciding it, 0x6853e and 0x6855c setting, 0x68562 the test, the post
        at 0x68578].
        """
        both_bits = Flag.PATH_A | Flag.PATH_B
        self.flags |= both_bits if both else (Flag.PATH_B if second else Flag.PATH_A)
        if self.flags & both_bits != both_bits:
            return None
        return self.feed(Event.BOTH_PATHS_READY)


# the nine handlers ---------------------------------------------------------------------


def _unexpected(procedure: Procedure, event: Event, stay: State) -> Step:
    """What every handler but state 6 does with an event it does not expect: keep the state."""
    return Step(event, procedure.state, stay, expected=False)


def _state_send_setup(procedure: Procedure, event: Event) -> Step:
    """Event 0 sends 0x73 and claims the link's procedure slot with 5. [fw acib 0x68588]

    The slot is the number at the link's +0x82: it is taken only while it reads zero and
    released only by the number that took it [fw acib 0x2515c, 0x25304].
    """
    if event != Event.BEGIN:
        return _unexpected(procedure, event, State.SEND_SETUP)
    return Step(event, State.SEND_SETUP, State.AWAIT_SETUP_ACCEPTED, Action.SEND_SETUP_REQUEST)


def _state_await_accepted(procedure: Procedure, event: Event) -> Step:
    """Two conditions, then 0x74 goes out. [fw acib 0x685f8]

    Event 1 sets the accepted flag and falls through to the link test; any other event gets
    as far as that test only if the flag is already set. The handler traces anything but
    events 1 and 2 [fw acib 0x68676], and the state stays 1 until the test passes.
    """
    expected = event in (Event.SETUP_ACCEPTED, Event.LINK_STATE_ADVANCED)
    if event == Event.SETUP_ACCEPTED:
        procedure.flags |= Flag.ACCEPTED
    elif not procedure.flags & Flag.ACCEPTED:
        return Step(event, State.AWAIT_SETUP_ACCEPTED, State.AWAIT_SETUP_ACCEPTED,
                    expected=expected)
    if not procedure.link_test:
        return Step(event, State.AWAIT_SETUP_ACCEPTED, State.AWAIT_SETUP_ACCEPTED,
                    expected=expected)
    return Step(event, State.AWAIT_SETUP_ACCEPTED, State.AWAIT_MODULE_CALL,
                Action.SEND_CONFIGURATION, expected=expected)


def _state_await_module_call(procedure: Procedure, event: Event) -> Step:
    """Event 3 builds the request block and hands it down. [fw acib 0x6869c]"""
    if event != Event.MODULE_CALL:
        return _unexpected(procedure, event, State.AWAIT_MODULE_CALL)
    return Step(event, State.AWAIT_MODULE_CALL, State.AWAIT_MODULE_RESULT,
                Action.BUILD_REQUEST_BLOCK)


def _state_await_module_result(procedure: Procedure, event: Event) -> Step:
    """Event 4 is the one the host hears about. [fw acib 0x68788]

    The event's argument is a pair, and the module's check on its second half decides it:
    1 means the mode is on and the host is told so, anything else means the attempt failed
    and the state goes back to 0. Either way the host gets one event, with the reason 0xFF,
    because no peer refused anything here.
    """
    if event != Event.MODULE_RESULT:
        return Step(event, State.AWAIT_MODULE_RESULT, State.SEND_SETUP, expected=False)
    ok = procedure.module_result == MODULE_RESULT_OK
    host = ModeChangeEvent(
        handle=procedure.handle,
        mode=Mode.ULLA_ON if ok else Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS if ok else Result.ERROR,
        reason=RejectReason.NOT_APPLICABLE,
    )
    after = State.AWAIT_SECOND_BEGIN if ok else State.SEND_SETUP
    return Step(event, State.AWAIT_MODULE_RESULT, after, host_event=host)


def _state_await_second_begin(procedure: Procedure, event: Event) -> Step:
    """Event 5 registers the first callback and moves on. [fw acib 0x687fc]"""
    if event != Event.BEGIN_PHASE_TWO:
        return _unexpected(procedure, event, State.AWAIT_SECOND_BEGIN)
    return Step(event, State.AWAIT_SECOND_BEGIN, State.AWAIT_THREE_FLAGS,
                Action.REGISTER_CALLBACK)


def _state_await_three_flags(procedure: Procedure, event: Event) -> Step:
    """Three flags have to be set before this state moves. [fw acib 0x6886c]

    Event 2 sets the link flag, event 6 says both paths reported, and the handler only goes
    on once bits 2, 3 and 4 are all set [fw acib 0x688ac]; until then it stays where it is.
    """
    expected = event in (Event.LINK_STATE_ADVANCED, Event.BOTH_PATHS_READY)
    if event == Event.LINK_STATE_ADVANCED:
        procedure.flags |= Flag.LINK
    if procedure.flags & THREE_FLAGS != THREE_FLAGS:
        return Step(event, State.AWAIT_THREE_FLAGS, State.AWAIT_THREE_FLAGS, expected=expected)
    return Step(event, State.AWAIT_THREE_FLAGS, State.AWAIT_CALLBACKS,
                Action.REGISTER_CALLBACKS, expected=expected)


def _state_reset_only(procedure: Procedure, event: Event) -> Step:
    """Whatever arrives, the state becomes 0. [fw acib 0x688f4, two instructions]"""
    return Step(event, State.RESET_ONLY, State.SEND_SETUP)


def _state_await_callbacks(procedure: Procedure, event: Event) -> Step:
    """Either of the two callbacks moves this on. [fw acib 0x688f8]"""
    if event not in (Event.CALLBACK_A, Event.CALLBACK_B):
        return _unexpected(procedure, event, State.AWAIT_CALLBACKS)
    return Step(event, State.AWAIT_CALLBACKS, State.ESTABLISHED)


def _state_established(procedure: Procedure, event: Event) -> Step:
    """The callbacks are tolerated, event 9 tears the whole thing down. [fw acib 0x68924]

    The teardown is the one place the host is told mode 0 with the success result, and the
    reason byte is the object's own +0x1C rather than 0xFF [fw acib 0x6896a].
    """
    if event in (Event.CALLBACK_A, Event.CALLBACK_B):
        return Step(event, State.ESTABLISHED, State.ESTABLISHED)
    if event != Event.STOP:
        return _unexpected(procedure, event, State.ESTABLISHED)
    host = ModeChangeEvent(
        handle=procedure.handle,
        mode=Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS,
        reason=procedure.stop_reason,
    )
    return Step(event, State.ESTABLISHED, State.SEND_SETUP, Action.TEAR_DOWN, host_event=host)


_HANDLERS: dict[State, Callable[[Procedure, Event], Step]] = {
    State.SEND_SETUP: _state_send_setup,
    State.AWAIT_SETUP_ACCEPTED: _state_await_accepted,
    State.AWAIT_MODULE_CALL: _state_await_module_call,
    State.AWAIT_MODULE_RESULT: _state_await_module_result,
    State.AWAIT_SECOND_BEGIN: _state_await_second_begin,
    State.AWAIT_THREE_FLAGS: _state_await_three_flags,
    State.RESET_ONLY: _state_reset_only,
    State.AWAIT_CALLBACKS: _state_await_callbacks,
    State.ESTABLISHED: _state_established,
}
HANDLER_ADDRESSES: dict[State, int] = {
    State.SEND_SETUP: 0x68588,
    State.AWAIT_SETUP_ACCEPTED: 0x685F8,
    State.AWAIT_MODULE_CALL: 0x6869C,
    State.AWAIT_MODULE_RESULT: 0x68788,
    State.AWAIT_SECOND_BEGIN: 0x687FC,
    State.AWAIT_THREE_FLAGS: 0x6886C,
    State.RESET_ONLY: 0x688F4,
    State.AWAIT_CALLBACKS: 0x688F8,
    State.ESTABLISHED: 0x68924,
}
"""The table at acib 0xf2664, entry by entry, so a reader can check any claim above."""
