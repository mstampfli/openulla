"""The controller's own procedures: the state machines that run the link-manager exchange.

This is one layer below openulla.session, which is the earbud's session manager. The
controller runs the exchange as a procedure with its own states, and it is the thing that
puts 0x73 and 0x74 on the air and that tells the host how the attempt ended.

There are **three** of them in this firmware, one per role, each with its own dispatcher,
its own table of handlers and its own state field in the procedure object:

| role | dispatcher | table | states | state field | re-entry guard |
|---|---|---|---|---|---|
| source | acib 0x6829c | acib 0xf2664 | 9 | +0x24 | bit 0 of +0x23 |
| sink | acib 0x67318 | acib 0xf2628 | 8 | +0x1F | the byte at +0x1C |
| third | acib 0x67ba4 | acib 0xf2648 | 7 | +0x1E | the byte at +0x1C |

The three state fields overlap in the object, so only one role is live at a time, which is
also how the installer treats them: it writes one of three sets of callbacks into the module
block and nothing else [fw acib 0x6218c, selecting on the byte at 0x1000df69].

Every dispatcher works the same way: it takes the object, an event number and one argument,
reads the state, refuses to run inside itself, calls the handler the table holds for that
state, and writes the handler's return value back as the new state. A handler that does not
expect an event traces it and returns its own number, which is how an unexpected event
becomes "stay here".

The numbers here are the firmware's. Most of the names are ours, because unlike the session
manager these machines have no name table in the image. Three strings do name parts of them,
and they are worth more than their length suggests [fw acib 0xf89ca, 0xf8a09, 0xf8a4c]:

    ULLA_TRANS: ERROR- lm_ulla_trans_sec_handle_setup_lmp_accepted
    ULLA_TRANS: ERROR- lm_ulla_trans_sec_handle_setup_lmp_not_accepted
    ULLA_TRANS: ERROR- lm_ulla_trans_source_ulla_teardown_init_handle_lmp_not_accepted

So the firmware calls each of these a **transaction**, it calls the first role **source** and
the third **sec**, and it has a phase called **ulla_teardown_init**. The third string names
the assert at acib 0x68504, whose sibling at 0x68514 is the handler for the accepted answer in
that phase: it requires the link to be in mode 0x36 and it posts event 6. Since the entry that
starts at state 4 is the one that puts the link in mode 0x36, and since what state 8 finally
reports is mode 0 with the success result, the second half of this machine is the teardown,
and the names below say so. The first two strings are asserts in the third role's slots for an
accepted and a not-accepted answer, which is as much as to say that role never sends 0x73.

"The module" below means the code that owns these procedures. All three roles live in one
block at acib 0x1000df68: its byte at +1 picks the role, +2 refines it, +0x84 holds the
module's own phase number, +0xA4 points at the single procedure object, and the callback slots
from +0xB8 to +0x11C are what the installer fills. A second structure at 0x1000329c holds the
device's ULLA state, including the two link handles at +0x1A and +0x1C and the two bytes that
gate the answer, and the responder handlers read both [fw acib 0x618f4 returning the first,
0x67b94 resolving a link through the second]. What the module is for beyond driving this
exchange is not traced, so it is not named here.

Where the source's events come from, each one proven at its posting site:

    0  the source entry point                        [fw acib 0x6842c, called from 0x66da0]
    1  an accepted PDU naming opcode 0x73            [fw acib 0x3c6ec -> 0x61fcc -> 0x684dc]
    2  the link's own state byte read 3 and set to 4  [fw acib 0x628f8 -> 0x68424]
    3  a call inside the module                      [fw acib 0x66db0]
    4  a {byte, word} pair from the module           [fw acib 0x619a4 -> 0x684e4]
    5  the teardown entry point                     [fw acib 0x68450, called from 0x66d72]
    6  the teardown's accepted answer, both paths    [fw acib 0x68514, posted at 0x68578]
    7  the first callback state 5 registers          [fw acib 0x66fe8]
    8  the second callback state 5 registers         [fw acib 0x67028]
    9  the teardown's last step, from slot +0xD8     [fw acib 0x684fc]

The sink's events come from the matching slots of its own set, and the slot numbers line up
with the source's: +0xE0 posts event 1, +0xEC event 2, +0xC8 event 3, +0xD4 event 4, +0xF0
event 5, +0xF4 event 6, +0xF8 event 7 and +0xD8 event 8 [fw acib 0x67706, 0x67584, 0x677aa,
0x677ba, 0x675a8, 0x675e0, 0x675e8, 0x675f0]. The source's +0xD8 posts 9 rather than 8, and
its +0xC8 only writes a trace line [fw acib 0x66e10], which is the one asymmetry worth
knowing about: see `SOURCE_EVENT_3_IS_UNREACHABLE`.

A not-accepted PDU naming 0x73 is not an event: it goes straight to the host as mode 0,
result 3 and the peer's own reason byte, and releases the link's procedure slot state 0
claimed [fw acib 0x3c7de -> 0x61ff0 -> 0x6848c]. That is the path `peer_refused` models.

One limit, stated rather than smoothed over: the request block a state-2 handler builds is
modelled by its offsets only, because what the calls it is handed to do with it was not
followed [fw acib 0x68748 onwards, 0x6799c onwards].
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Generic, TypeVar

from .codes import HciError, Mode, RejectReason, Result
from .errors import ProtocolError
from .hci import ModeChangeEvent

StateT = TypeVar("StateT", bound=IntEnum)

SOURCE_EVENT_3_IS_UNREACHABLE = True
"""No path in this image posts the source's event 3, so its state 2 cannot advance.

The sink posts its event 3 from slot +0xC8; in the source's set that slot holds a function
that only writes a trace line [fw acib 0x66e10]. The one function that does post event 3 is
acib 0x66db0, and no branch reaches it and no word in the image equals its address, so what
calls it is not in this build. Every other event of the source's machine has a reachable
poster. Stated here because it bounds what a source built from this firmware can do, and
because an implementation of the source role has to supply that step itself.
"""


class State(IntEnum):
    """The source's nine states, named after what their handler does."""

    SEND_SETUP = 0
    AWAIT_SETUP_ACCEPTED = 1
    AWAIT_MODULE_CALL = 2
    AWAIT_MODULE_RESULT = 3
    AWAIT_TEARDOWN_BEGIN = 4
    AWAIT_TEARDOWN_ACCEPTED = 5
    RESET_ONLY = 6
    AWAIT_TEARDOWN_STEP = 7
    AWAIT_TEARDOWN_DONE = 8


class SinkState(IntEnum):
    """The sink's eight states."""

    AWAIT_SETUP_REQUEST = 0
    AWAIT_BOTH_LINKS = 1
    AWAIT_MODULE_CALL = 2
    AWAIT_MODULE_RESULT = 3
    AWAIT_TEARDOWN_BEGIN = 4
    AWAIT_TEARDOWN_ACCEPTED = 5
    AWAIT_TEARDOWN_STEP = 6
    AWAIT_TEARDOWN_DONE = 7


class ThirdState(IntEnum):
    """The third role's seven states.

    It is the sink's machine with the state that waits for both links taken out: the same
    request block, handed to the same three calls, and the same host report, with every event
    number after the first one lower [fw acib 0x6809e against the sink's 0x679a0].
    """

    AWAIT_SETUP_REQUEST = 0
    AWAIT_MODULE_CALL = 1
    AWAIT_MODULE_RESULT = 2
    AWAIT_TEARDOWN_BEGIN = 3
    AWAIT_TEARDOWN_ACCEPTED = 4
    AWAIT_TEARDOWN_STEP = 5
    AWAIT_TEARDOWN_DONE = 6


class Event(IntEnum):
    """The event numbers, named after the source's posting sites.

    The sink uses the same numbering from the same slots, with one shift: its stop event is
    8, where the source's is 9. The third role's are one lower again from its second state
    on, because it has one state fewer.
    """

    BEGIN = 0
    SETUP_ACCEPTED = 1
    LINK_STATE_ADVANCED = 2
    MODULE_CALL = 3
    MODULE_RESULT = 4
    TEARDOWN_BEGIN = 5
    TEARDOWN_ACCEPTED = 6
    CALLBACK_A = 7
    CALLBACK_B = 8
    TEARDOWN_COMPLETE = 9


class Flag(IntEnum):
    """Bits of the source object's flag byte at +0x23, which the initialiser clears.

    Bits 5, 6 and 7 are kept across a run [fw acib 0x6836a masks with 0xE0]; what they mean
    is not traced, so nothing here touches them. The sink keeps the one flag it needs as a
    whole byte at +0x1D instead [fw acib 0x67880].
    """

    BUSY = 0x01  # the dispatcher's re-entry guard [fw acib 0x682be, cleared 0x682e6]
    ACCEPTED = 0x02  # set by event 1 in state 1 [fw acib 0x68616]
    PATH_A = 0x04  # set by the two-path callback [fw acib 0x6853e, 0x6855c]
    PATH_B = 0x08  # the same callback, the other path
    LINK = 0x10  # set by event 2 in state 5 [fw acib 0x6888c]


THREE_FLAGS = Flag.PATH_A | Flag.PATH_B | Flag.LINK
MODULE_RESULT_OK = 1  # what the module's check returns on success [fw acib 0x6879e, 0x67a10]
DEFAULT_PARAMETERS = (3, 8, 8, 8)
"""The source context's +0x14 to +0x17, as the initialiser leaves them [fw acib 0x6832c].

The same four bytes can be set from a caller's buffer through the module's own +0x10C entry
[fw acib 0x68300, installed at 0x6246c], and state 2 copies them into the request block at
its +0x6C and +0x6D to +0x6F [fw acib 0x6871a onwards]. What they mean is not traced.
"""
SIBLING_REFUSAL = HciError.LMP_PDU_NOT_ALLOWED
"""What the sink refuses with when it cannot hand the request to its sibling.

Not one of the ULLA reasons: it is the standard code 0x24, and the firmware's own reason
formatter has no name for it, so its log prints the invalid-reason text
[fw acib 0x6783c sending it, rkof 0x2774c naming it].
"""


class Action(IntEnum):
    """What a handler does besides returning a state."""

    NOTHING = 0
    SEND_SETUP_REQUEST = 1  # LMP 0x73 [fw acib 0x685de]
    SEND_CONFIGURATION = 2  # LMP 0x74 [fw acib 0x6866c]
    BUILD_REQUEST_BLOCK = 3  # [fw acib 0x686a6 onwards, sink 0x67928 onwards]
    REGISTER_CALLBACK = 4  # [fw acib 0x6882e, sink 0x67aca]
    REGISTER_CALLBACKS = 5  # both of them [fw acib 0x688c6, 0x688d4]
    TEAR_DOWN = 6  # [fw acib 0x68948 onwards, sink 0x67b30 onwards]
    SEND_ACCEPTED = 7  # [fw acib 0x302f8 and 0x3d1c6]
    REFUSE = 8  # not accepted, with the reason in the step [fw acib 0x3c934]
    FORWARD_TO_SIBLING = 9  # [fw acib 0x303a8]
    CLAIM_SLOT = 10  # the link's procedure slot at its +0x82 [fw acib 0x2515c]


@dataclass(frozen=True)
class Step(Generic[StateT]):
    """One event, and everything the firmware does with it."""

    event: Event
    before: StateT
    after: StateT
    action: Action = Action.NOTHING
    host_event: ModeChangeEvent | None = None
    expected: bool = True
    reason: int | None = None  # what a refusing step puts on the air


@dataclass
class Procedure:
    """One attempt at the source. The module keeps a single one, at its block's +0xA4.

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
    history: list[Step[State]] = field(default_factory=list)

    # the two entry points ------------------------------------------------------------

    def begin(self) -> Step[State]:
        """Start the setup: state 0 and event 0, with the link in mode 0x35
        [fw acib 0x6842c]."""
        self.state = State.SEND_SETUP
        self.flags &= ~0x1F
        return self.feed(Event.BEGIN)

    def begin_teardown(self) -> Step[State]:
        """Start the teardown: state 4 and event 5, with the link in mode 0x36.

        The firmware reaches state 4 both ways, from the setup's own last state and from
        this entry [fw acib 0x68450, and 0x687a2 arriving there on success], which is why
        state 4 is where a link that is up sits and waits.
        """
        self.state = State.AWAIT_TEARDOWN_BEGIN
        return self.feed(Event.TEARDOWN_BEGIN)

    # the dispatcher -----------------------------------------------------------------

    def feed(self, event: Event) -> Step[State]:
        """Run one event through the handler of the current state. [fw acib 0x6829c]"""
        if self.flags & Flag.BUSY:
            raise ProtocolError("the dispatcher refuses to run inside itself")
        self.flags |= Flag.BUSY
        try:
            step = _HANDLERS[self.state](self, event)
        finally:
            self.flags &= ~Flag.BUSY
        self.state = step.after
        self.history.append(step)
        return step

    def feed_all(self, events: Iterable[Event]) -> list[Step[State]]:
        return [self.feed(event) for event in events]

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

        The link's own mode byte decides it: 0x35, the setup, gives "failed to be
        established", anything else gives the supervision-timeout result
        [fw acib 0x68390, the two assignments at 0x683c2 and 0x683fe, the report at 0x6841c].
        """
        return ModeChangeEvent(
            handle=self.handle,
            mode=Mode.BT_ACL_ACTIVE,
            result=Result.FAIL_TO_BE_ESTABLISHED if first_phase else Result.LSTO_ERROR,
            reason=RejectReason.NOT_APPLICABLE,
        )

    def path_reported(self, *, both: bool = False, second: bool = False) -> Step[State] | None:
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
        return self.feed(Event.TEARDOWN_ACCEPTED)


# the source's nine handlers -------------------------------------------------------------


def _unexpected(procedure: Procedure, event: Event, stay: State) -> Step[State]:
    """What every handler but state 6 does with an event it does not expect: keep the state."""
    return Step(event, procedure.state, stay, expected=False)


def _state_send_setup(procedure: Procedure, event: Event) -> Step[State]:
    """Event 0 sends 0x73 and claims the link's procedure slot with 5. [fw acib 0x68588]

    The slot is the number at the link's +0x82: it is taken only while it reads zero and
    released only by the number that took it [fw acib 0x2515c, 0x25304].
    """
    if event != Event.BEGIN:
        return _unexpected(procedure, event, State.SEND_SETUP)
    return Step(event, State.SEND_SETUP, State.AWAIT_SETUP_ACCEPTED, Action.SEND_SETUP_REQUEST)


def _state_await_accepted(procedure: Procedure, event: Event) -> Step[State]:
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


def _state_await_module_call(procedure: Procedure, event: Event) -> Step[State]:
    """Event 3 builds the request block and hands it down. [fw acib 0x6869c]

    Nothing in this build posts that event to the source, so this transition is the one the
    firmware cannot take: see SOURCE_EVENT_3_IS_UNREACHABLE.
    """
    if event != Event.MODULE_CALL:
        return _unexpected(procedure, event, State.AWAIT_MODULE_CALL)
    return Step(event, State.AWAIT_MODULE_CALL, State.AWAIT_MODULE_RESULT,
                Action.BUILD_REQUEST_BLOCK)


def _state_await_module_result(procedure: Procedure, event: Event) -> Step[State]:
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
    after = State.AWAIT_TEARDOWN_BEGIN if ok else State.SEND_SETUP
    return Step(event, State.AWAIT_MODULE_RESULT, after, host_event=host)


def _state_await_teardown_begin(procedure: Procedure, event: Event) -> Step[State]:
    """Event 5 registers the first callback and moves on. [fw acib 0x687fc]"""
    if event != Event.TEARDOWN_BEGIN:
        return _unexpected(procedure, event, State.AWAIT_TEARDOWN_BEGIN)
    return Step(event, State.AWAIT_TEARDOWN_BEGIN, State.AWAIT_TEARDOWN_ACCEPTED,
                Action.REGISTER_CALLBACK)


def _state_await_teardown_accepted(procedure: Procedure, event: Event) -> Step[State]:
    """Three flags have to be set before this state moves. [fw acib 0x6886c]

    Event 2 sets the link flag, event 6 says both paths reported, and the handler only goes
    on once bits 2, 3 and 4 are all set [fw acib 0x688ac]; until then it stays where it is.
    """
    expected = event in (Event.LINK_STATE_ADVANCED, Event.TEARDOWN_ACCEPTED)
    if event == Event.LINK_STATE_ADVANCED:
        procedure.flags |= Flag.LINK
    if procedure.flags & THREE_FLAGS != THREE_FLAGS:
        return Step(event, State.AWAIT_TEARDOWN_ACCEPTED, State.AWAIT_TEARDOWN_ACCEPTED,
                    expected=expected)
    return Step(event, State.AWAIT_TEARDOWN_ACCEPTED, State.AWAIT_TEARDOWN_STEP,
                Action.REGISTER_CALLBACKS, expected=expected)


def _state_reset_only(procedure: Procedure, event: Event) -> Step[State]:
    """Whatever arrives, the state becomes 0. [fw acib 0x688f4, two instructions]"""
    return Step(event, State.RESET_ONLY, State.SEND_SETUP)


def _state_await_teardown_step(procedure: Procedure, event: Event) -> Step[State]:
    """Either of the two callbacks moves this on. [fw acib 0x688f8]"""
    if event not in (Event.CALLBACK_A, Event.CALLBACK_B):
        return _unexpected(procedure, event, State.AWAIT_TEARDOWN_STEP)
    return Step(event, State.AWAIT_TEARDOWN_STEP, State.AWAIT_TEARDOWN_DONE)


def _state_await_teardown_done(procedure: Procedure, event: Event) -> Step[State]:
    """The callbacks are tolerated, and event 9 finishes the teardown. [fw acib 0x68924]

    This is the one place the host is told mode 0 with the success result, and the
    reason byte is the object's own +0x1C rather than 0xFF [fw acib 0x6896a].
    """
    if event in (Event.CALLBACK_A, Event.CALLBACK_B):
        return Step(event, State.AWAIT_TEARDOWN_DONE, State.AWAIT_TEARDOWN_DONE)
    if event != Event.TEARDOWN_COMPLETE:
        return _unexpected(procedure, event, State.AWAIT_TEARDOWN_DONE)
    host = ModeChangeEvent(
        handle=procedure.handle,
        mode=Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS,
        reason=procedure.stop_reason,
    )
    return Step(event, State.AWAIT_TEARDOWN_DONE, State.SEND_SETUP, Action.TEAR_DOWN,
                host_event=host)


_HANDLERS: dict[State, Callable[[Procedure, Event], Step[State]]] = {
    State.SEND_SETUP: _state_send_setup,
    State.AWAIT_SETUP_ACCEPTED: _state_await_accepted,
    State.AWAIT_MODULE_CALL: _state_await_module_call,
    State.AWAIT_MODULE_RESULT: _state_await_module_result,
    State.AWAIT_TEARDOWN_BEGIN: _state_await_teardown_begin,
    State.AWAIT_TEARDOWN_ACCEPTED: _state_await_teardown_accepted,
    State.RESET_ONLY: _state_reset_only,
    State.AWAIT_TEARDOWN_STEP: _state_await_teardown_step,
    State.AWAIT_TEARDOWN_DONE: _state_await_teardown_done,
}


@dataclass
class SinkProcedure:
    """One attempt at the sink: the same shape, answering instead of asking.

    Its first state is the one that receives 0x73, which is where the four checks of
    protocol.md 5.4 are made [fw acib 0x63448 calling 0x675f4, then 0x67588 posting event 0].
    """

    handle: int = 0
    state: SinkState = SinkState.AWAIT_SETUP_REQUEST
    busy: bool = False
    ready: bool = False
    """The byte at +0x1D, set by event 1 [fw acib 0x67880]."""
    role_byte_is_one: bool = False
    """The module's second role byte. When it reads 1 the request is not forwarded at all
    and the state goes straight to 2 [fw acib 0x677ec]."""
    sibling_accepts: bool = True
    """Whether handing the request to the second link succeeds [fw acib 0x303a8]."""
    can_answer: bool = True
    """Whether the link can still carry a reply: its state byte 0xC and its flag at +0x185
    clear [fw acib 0x6782a, 0x67832]."""
    both_links_ready: bool = True
    """What state 1 asks of both links before the accepted PDU goes out [fw acib 0x67896]."""
    module_result: int = MODULE_RESULT_OK
    stop_reason: int = RejectReason.NOT_APPLICABLE
    """The byte at +0x1E, which the teardown reports to the host [fw acib 0x67b5a]."""
    history: list[Step[SinkState]] = field(default_factory=list)

    def begin(self) -> Step[SinkState]:
        """A setup request arrived and passed the checks: state 0, event 0 [fw acib 0x67588]."""
        self.state = SinkState.AWAIT_SETUP_REQUEST
        return self.feed(Event.BEGIN)

    def begin_teardown(self) -> Step[SinkState]:
        """The second entry, which sets the state to 4 and posts event 5 [fw acib 0x675ac]."""
        self.state = SinkState.AWAIT_TEARDOWN_BEGIN
        return self.feed(Event.TEARDOWN_BEGIN)

    def feed(self, event: Event) -> Step[SinkState]:
        """Run one event through the handler of the current state. [fw acib 0x67318]"""
        if self.busy:
            raise ProtocolError("the dispatcher refuses to run inside itself")
        self.busy = True
        try:
            step = _SINK_HANDLERS[self.state](self, event)
        finally:
            self.busy = False
        self.state = step.after
        self.history.append(step)
        return step

    def feed_all(self, events: Iterable[Event]) -> list[Step[SinkState]]:
        return [self.feed(event) for event in events]


def _sink_unexpected(sink: SinkProcedure, event: Event, stay: SinkState) -> Step[SinkState]:
    return Step(event, sink.state, stay, expected=False)


def _sink_await_request(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 0: either keep it here, hand it to the sibling, or refuse. [fw acib 0x677c8]

    With the module's second role byte at 1 the request is answered by this earbud alone:
    it claims the link's procedure slot and goes to state 2. Otherwise it is handed to the
    second link, and the state waits for both. If that handing fails, the request is refused
    with the standard 0x24 and not with an ULLA reason, and only when the link can still
    carry a reply [fw acib 0x677fa, 0x6780e, 0x6783c].
    """
    if event != Event.BEGIN:
        return _sink_unexpected(sink, event, SinkState.AWAIT_SETUP_REQUEST)
    if sink.role_byte_is_one:
        return Step(event, SinkState.AWAIT_SETUP_REQUEST, SinkState.AWAIT_MODULE_CALL,
                    Action.CLAIM_SLOT)
    if sink.sibling_accepts:
        return Step(event, SinkState.AWAIT_SETUP_REQUEST, SinkState.AWAIT_BOTH_LINKS,
                    Action.FORWARD_TO_SIBLING)
    if not sink.can_answer:
        return Step(event, SinkState.AWAIT_SETUP_REQUEST, SinkState.AWAIT_SETUP_REQUEST)
    return Step(event, SinkState.AWAIT_SETUP_REQUEST, SinkState.AWAIT_SETUP_REQUEST,
                Action.REFUSE, reason=SIBLING_REFUSAL)


def _sink_await_both_links(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 1 claims the slot on both links; the accepted PDU waits for both. [fw acib 0x67858]

    This is the hold protocol.md 5.5 describes, from the inside: the flag at +0x1D and the
    two link tests all have to pass before 0x302f8 sends the answer [fw acib 0x67890 onwards].
    """
    expected = event in (Event.SETUP_ACCEPTED, Event.LINK_STATE_ADVANCED)
    if event == Event.SETUP_ACCEPTED:
        sink.ready = True
    if not sink.ready or not sink.both_links_ready:
        return Step(event, SinkState.AWAIT_BOTH_LINKS, SinkState.AWAIT_BOTH_LINKS,
                    expected=expected)
    return Step(event, SinkState.AWAIT_BOTH_LINKS, SinkState.AWAIT_MODULE_CALL,
                Action.SEND_ACCEPTED, expected=expected)


def _sink_await_module_call(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 2 sends the accepted PDU, event 3 builds the request block. [fw acib 0x678f8]"""
    if event == Event.LINK_STATE_ADVANCED:
        return Step(event, SinkState.AWAIT_MODULE_CALL, SinkState.AWAIT_MODULE_CALL,
                    Action.SEND_ACCEPTED)
    if event != Event.MODULE_CALL:
        return _sink_unexpected(sink, event, SinkState.AWAIT_MODULE_CALL)
    return Step(event, SinkState.AWAIT_MODULE_CALL, SinkState.AWAIT_MODULE_RESULT,
                Action.BUILD_REQUEST_BLOCK)


def _sink_await_module_result(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 4 reports to the host and, on success, goes back to idle. [fw acib 0x679f8]

    The sink's procedure is over at that point, unlike the source's: a responder has nothing
    left to drive, so the state returns to 0 and the second entry starts the rest
    [fw acib 0x67a20 returning 0, 0x675ac entering at 4].
    """
    if event != Event.MODULE_RESULT:
        return _sink_unexpected(sink, event, SinkState.AWAIT_MODULE_RESULT)
    ok = sink.module_result == MODULE_RESULT_OK
    host = ModeChangeEvent(
        handle=sink.handle,
        mode=Mode.ULLA_ON if ok else Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS if ok else Result.ERROR,
        reason=RejectReason.NOT_APPLICABLE,
    )
    after = SinkState.AWAIT_SETUP_REQUEST if ok else SinkState.AWAIT_MODULE_RESULT
    return Step(event, SinkState.AWAIT_MODULE_RESULT, after, host_event=host)


def _sink_await_teardown_begin(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 5 moves on. [fw acib 0x67a6c]"""
    if event != Event.TEARDOWN_BEGIN:
        return _sink_unexpected(sink, event, SinkState.AWAIT_TEARDOWN_BEGIN)
    return Step(event, SinkState.AWAIT_TEARDOWN_BEGIN, SinkState.AWAIT_TEARDOWN_ACCEPTED)


def _sink_await_teardown_accepted(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 6 registers a callback. [fw acib 0x67aa0]"""
    if event != Event.TEARDOWN_ACCEPTED:
        return _sink_unexpected(sink, event, SinkState.AWAIT_TEARDOWN_ACCEPTED)
    return Step(event, SinkState.AWAIT_TEARDOWN_ACCEPTED, SinkState.AWAIT_TEARDOWN_STEP,
                Action.REGISTER_CALLBACK)


def _sink_await_teardown_step(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 7, one call. [fw acib 0x67af0]"""
    if event != Event.CALLBACK_A:
        return _sink_unexpected(sink, event, SinkState.AWAIT_TEARDOWN_STEP)
    return Step(event, SinkState.AWAIT_TEARDOWN_STEP, SinkState.AWAIT_TEARDOWN_DONE)


def _sink_await_teardown_done(sink: SinkProcedure, event: Event) -> Step[SinkState]:
    """Event 8 tears it down and reports mode 0 with the success result. [fw acib 0x67b18]"""
    if event != Event.CALLBACK_B:
        return _sink_unexpected(sink, event, SinkState.AWAIT_TEARDOWN_DONE)
    host = ModeChangeEvent(
        handle=sink.handle,
        mode=Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS,
        reason=sink.stop_reason,
    )
    return Step(event, SinkState.AWAIT_TEARDOWN_DONE, SinkState.AWAIT_SETUP_REQUEST,
                Action.TEAR_DOWN, host_event=host)


_SINK_HANDLERS: dict[SinkState, Callable[[SinkProcedure, Event], Step[SinkState]]] = {
    SinkState.AWAIT_SETUP_REQUEST: _sink_await_request,
    SinkState.AWAIT_BOTH_LINKS: _sink_await_both_links,
    SinkState.AWAIT_MODULE_CALL: _sink_await_module_call,
    SinkState.AWAIT_MODULE_RESULT: _sink_await_module_result,
    SinkState.AWAIT_TEARDOWN_BEGIN: _sink_await_teardown_begin,
    SinkState.AWAIT_TEARDOWN_ACCEPTED: _sink_await_teardown_accepted,
    SinkState.AWAIT_TEARDOWN_STEP: _sink_await_teardown_step,
    SinkState.AWAIT_TEARDOWN_DONE: _sink_await_teardown_done,
}

@dataclass
class ThirdProcedure:
    """The third role: a second responder, on a second link.

    The receive path picks it for a setup request that arrives on a link other than the one
    the ULLA state block names at its +0x1C, and ignores a second request on that one
    [fw acib 0x634e4]. So an earbud can hold two responder procedures at once. What the role
    is *for* is not traced; what it does is.

    Its host events carry that stored handle rather than its own link's, which is the one
    place it differs from the sink beyond the missing state [fw acib 0x68162].
    """

    handle: int = 0
    """The handle this machine reports with: the stored link's, not the incoming one's."""
    state: ThirdState = ThirdState.AWAIT_SETUP_REQUEST
    busy: bool = False
    module_result: int = MODULE_RESULT_OK
    stop_reason: int = RejectReason.NOT_APPLICABLE
    """The byte at +0x1D, which its second entry sets and its teardown reports
    [fw acib 0x67e00, 0x6825c]."""
    history: list[Step[ThirdState]] = field(default_factory=list)

    def begin(self) -> Step[ThirdState]:
        """State 0, event 0, with the link put into mode 0x35 [fw acib 0x67dd8]."""
        self.state = ThirdState.AWAIT_SETUP_REQUEST
        return self.feed(Event.BEGIN)

    def begin_teardown(self) -> Step[ThirdState]:
        """Its second entry: state 3 and event 4, both one lower than the sink's.

        It puts the link into mode 0x36 with a callback and keeps its third argument as the
        reason the teardown will report [fw acib 0x67dfc].
        """
        self.state = ThirdState.AWAIT_TEARDOWN_BEGIN
        return self.feed(Event.MODULE_RESULT)

    def feed(self, event: Event) -> Step[ThirdState]:
        """Run one event through the handler of the current state. [fw acib 0x67ba4]"""
        if self.busy:
            raise ProtocolError("the dispatcher refuses to run inside itself")
        self.busy = True
        try:
            step = _THIRD_HANDLERS[self.state](self, event)
        finally:
            self.busy = False
        self.state = step.after
        self.history.append(step)
        return step

    def feed_all(self, events: Iterable[Event]) -> list[Step[ThirdState]]:
        return [self.feed(event) for event in events]


def _third_unexpected(third: ThirdProcedure, event: Event, stay: ThirdState) -> Step[ThirdState]:
    return Step(event, third.state, stay, expected=False)


def _third_await_request(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 0 claims the link's slot and moves on, answering nobody. [fw acib 0x67fac]

    Unlike the sink's first state it neither sends a reply nor hands anything to a second
    link [fw acib 0x67fbe].
    """
    if event != Event.BEGIN:
        return _third_unexpected(third, event, ThirdState.AWAIT_SETUP_REQUEST)
    return Step(event, ThirdState.AWAIT_SETUP_REQUEST, ThirdState.AWAIT_MODULE_CALL,
                Action.CLAIM_SLOT)


def _third_await_module_call(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 1 sends the accepted PDU, event 2 builds the request block. [fw acib 0x67fec]"""
    if event == Event.SETUP_ACCEPTED:
        return Step(event, ThirdState.AWAIT_MODULE_CALL, ThirdState.AWAIT_MODULE_CALL,
                    Action.SEND_ACCEPTED)
    if event != Event.LINK_STATE_ADVANCED:
        return _third_unexpected(third, event, ThirdState.AWAIT_MODULE_CALL)
    return Step(event, ThirdState.AWAIT_MODULE_CALL, ThirdState.AWAIT_MODULE_RESULT,
                Action.BUILD_REQUEST_BLOCK)


def _third_await_module_result(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 3 reports to the host and, on success, goes back to idle. [fw acib 0x68104]"""
    if event != Event.MODULE_CALL:
        return _third_unexpected(third, event, ThirdState.AWAIT_MODULE_RESULT)
    ok = third.module_result == MODULE_RESULT_OK
    host = ModeChangeEvent(
        handle=third.handle,
        mode=Mode.ULLA_ON if ok else Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS if ok else Result.ERROR,
        reason=RejectReason.NOT_APPLICABLE,
    )
    after = ThirdState.AWAIT_SETUP_REQUEST if ok else ThirdState.AWAIT_MODULE_RESULT
    return Step(event, ThirdState.AWAIT_MODULE_RESULT, after, host_event=host)


def _third_await_teardown_begin(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 4 moves on. [fw acib 0x68184]"""
    if event != Event.MODULE_RESULT:
        return _third_unexpected(third, event, ThirdState.AWAIT_TEARDOWN_BEGIN)
    return Step(event, ThirdState.AWAIT_TEARDOWN_BEGIN, ThirdState.AWAIT_TEARDOWN_ACCEPTED)


def _third_await_teardown_accepted(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 5 registers a callback. [fw acib 0x681b4]"""
    if event != Event.TEARDOWN_BEGIN:
        return _third_unexpected(third, event, ThirdState.AWAIT_TEARDOWN_ACCEPTED)
    return Step(event, ThirdState.AWAIT_TEARDOWN_ACCEPTED, ThirdState.AWAIT_TEARDOWN_STEP,
                Action.REGISTER_CALLBACK)


def _third_await_teardown_step(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 6, one call. [fw acib 0x681f8]"""
    if event != Event.TEARDOWN_ACCEPTED:
        return _third_unexpected(third, event, ThirdState.AWAIT_TEARDOWN_STEP)
    return Step(event, ThirdState.AWAIT_TEARDOWN_STEP, ThirdState.AWAIT_TEARDOWN_DONE)


def _third_await_teardown_done(third: ThirdProcedure, event: Event) -> Step[ThirdState]:
    """Event 7 tears it down and reports mode 0 with the success result. [fw acib 0x6821c]"""
    if event != Event.CALLBACK_A:
        return _third_unexpected(third, event, ThirdState.AWAIT_TEARDOWN_DONE)
    host = ModeChangeEvent(
        handle=third.handle,
        mode=Mode.BT_ACL_ACTIVE,
        result=Result.SUCCESS,
        reason=third.stop_reason,
    )
    return Step(event, ThirdState.AWAIT_TEARDOWN_DONE, ThirdState.AWAIT_SETUP_REQUEST,
                Action.TEAR_DOWN, host_event=host)


_THIRD_HANDLERS: dict[ThirdState, Callable[[ThirdProcedure, Event], Step[ThirdState]]] = {
    ThirdState.AWAIT_SETUP_REQUEST: _third_await_request,
    ThirdState.AWAIT_MODULE_CALL: _third_await_module_call,
    ThirdState.AWAIT_MODULE_RESULT: _third_await_module_result,
    ThirdState.AWAIT_TEARDOWN_BEGIN: _third_await_teardown_begin,
    ThirdState.AWAIT_TEARDOWN_ACCEPTED: _third_await_teardown_accepted,
    ThirdState.AWAIT_TEARDOWN_STEP: _third_await_teardown_step,
    ThirdState.AWAIT_TEARDOWN_DONE: _third_await_teardown_done,
}

HANDLER_ADDRESSES: dict[State, int] = {
    State.SEND_SETUP: 0x68588,
    State.AWAIT_SETUP_ACCEPTED: 0x685F8,
    State.AWAIT_MODULE_CALL: 0x6869C,
    State.AWAIT_MODULE_RESULT: 0x68788,
    State.AWAIT_TEARDOWN_BEGIN: 0x687FC,
    State.AWAIT_TEARDOWN_ACCEPTED: 0x6886C,
    State.RESET_ONLY: 0x688F4,
    State.AWAIT_TEARDOWN_STEP: 0x688F8,
    State.AWAIT_TEARDOWN_DONE: 0x68924,
}
"""The table at acib 0xf2664, entry by entry, so a reader can check any claim above."""
SINK_HANDLER_ADDRESSES: dict[SinkState, int] = {
    SinkState.AWAIT_SETUP_REQUEST: 0x677C8,
    SinkState.AWAIT_BOTH_LINKS: 0x67858,
    SinkState.AWAIT_MODULE_CALL: 0x678F8,
    SinkState.AWAIT_MODULE_RESULT: 0x679F8,
    SinkState.AWAIT_TEARDOWN_BEGIN: 0x67A6C,
    SinkState.AWAIT_TEARDOWN_ACCEPTED: 0x67AA0,
    SinkState.AWAIT_TEARDOWN_STEP: 0x67AF0,
    SinkState.AWAIT_TEARDOWN_DONE: 0x67B18,
}
"""The table at acib 0xf2628."""
THIRD_HANDLER_ADDRESSES: dict[ThirdState, int] = {
    ThirdState.AWAIT_SETUP_REQUEST: 0x67FAC,
    ThirdState.AWAIT_MODULE_CALL: 0x67FEC,
    ThirdState.AWAIT_MODULE_RESULT: 0x68104,
    ThirdState.AWAIT_TEARDOWN_BEGIN: 0x68184,
    ThirdState.AWAIT_TEARDOWN_ACCEPTED: 0x681B4,
    ThirdState.AWAIT_TEARDOWN_STEP: 0x681F8,
    ThirdState.AWAIT_TEARDOWN_DONE: 0x6821C,
}
"""The third role's table at acib 0xf2648. Its entry points are acib 0x67dd8, which sets the
state to 0 and posts event 0, and 0x67dfc, which sets it to 3 and posts event 4."""
