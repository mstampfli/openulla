#!/usr/bin/env python3
"""Run a whole ULLA setup against the simulated earbud and print every byte of it.

Nothing here needs hardware: the sink is openulla.sink, which answers the way the earbud's
firmware does wherever that is proven, and the internal events are fed in the order
docs/protocol.md section 3.4 gives. Use it to see the frames an implementation sends, to
compare them against a capture, or to check that a change to the library still negotiates.

    python3 tools/ulla_demo.py
    python3 tools/ulla_demo.py --mode 1 --reject busy
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import bands, lmp, sim
from openulla.controller import AcceptChecks, Responder
from openulla.hci import UllaSetup
from openulla.session import Event
from openulla.source import ControllerCommands

REJECTIONS = {
    "none": AcceptChecks(),
    "feature": AcceptChecks(feature_enabled=False),
    "notready": AcceptChecks(object_ready=False),
    "busy": AcceptChecks(busy=True),
    "link": AcceptChecks(link_state=6),
}


def show(label, data):
    print(f"  {label:<34} {data.hex(' ')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", type=lambda s: int(s, 0), default=0, help="mode byte of 0x36")
    ap.add_argument("--handle", type=lambda s: int(s, 0), default=0x000B, help="connection handle")
    ap.add_argument("--reject", choices=sorted(REJECTIONS), default="none",
                    help="make the peer controller refuse at one of its four checks")
    ap.add_argument("--bands", default="1,2,5,6,7",
                    help="band indexes this source allows (default 1,2,5,6,7)")
    ap.add_argument("--features", type=lambda s: int(s, 0), default=0x60,
                    help="the feature word this source advertises (default 0x60: bits 5 and 6)")
    args = ap.parse_args()

    link = sim.connect()
    commands = ControllerCommands(args.handle)
    mask = ControllerCommands.mask_for(tuple(int(b) for b in args.bands.split(",")))

    print("1. the host opens AAP service 4 on L2CAP PSM 0x1001")
    reply = link.source.open_service(timeout=None)
    show("open request", link.transport.sent[-1])
    print(f"     accessory version {reply.major}.{reply.minor}, status {reply.status}")

    print("2. the host asks for a session with itself")
    link.source.start_session(args.mode)
    show("start session, opcode 0x36", link.transport.sent[-1])
    sibling_frames = [a for a in link.sink.actions if a.kind == "sibling"]
    show("the bud asks its sibling", sibling_frames[-1].data)
    link.feed_all(Event.APP_SETUP_COMPLETE, Event.SUPERVISION_TIMEOUT_UPDATED)
    link.feed(Event.PEER_SESSION_START_RSP)
    status = link.source.read_status(timeout=None)
    print(f"     status reply 0x{status:04x}, manager now in {link.sink.state.name}")

    print("3. the host subscribes to the report classes")
    link.source.subscribe_reports()
    show("wrapper 0x17, class 0x00200000", link.transport.sent[-1])

    print("4. the local audio path quiesces, and the controller is told to change mode")
    link.quiesce()
    show("HCI 0xFF77, band commands on", commands.enable_band_commands())
    first, second = commands.announce_bands(mask)
    show(f"HCI 0xFF79, mask 0x{mask:02x}", first)
    show("HCI 0xFF7A, the same mask", second)
    print(f"     that mask names bands {bands.mask_bands(mask)}")
    show("HCI 0xFE50, start the transition",
         commands.start_transition(config_word=0x0100, flags=0x03))

    print("5. controller to controller, on the existing ACL link")
    responder = Responder(checks=REJECTIONS[args.reject], features=args.features)
    before = len(link.pdus)
    reply = link.exchange(lmp.Features(args.features).to_bytes(), responder)
    for direction, pdu in link.pdus[before:]:
        show(f"LMP {direction}, the feature exchange", pdu)
    peer_word = lmp.Features.from_bytes(reply).word
    print(f"     the peer answers 0x{peer_word:08x}; band messages need bit "
          f"{lmp.FEATURE_BIT_BANDS} on both sides: "
          f"{lmp.agreed(args.features, peer_word, lmp.FEATURE_BIT_BANDS)}")
    before = len(link.pdus)
    link.exchange(lmp.AllowedBands(mask).to_bytes(), responder)
    for direction, pdu in link.pdus[before:]:
        show(f"LMP {direction}, the allowed-band mask", pdu)
    setup = UllaSetup(handle=args.handle, extra_flags=0, config_word=0x0100, flags=0x03)
    before = len(link.pdus)
    event = link.controller_setup(setup, responder=responder)
    for direction, pdu in link.pdus[before:]:
        show(f"LMP {direction}, the setup", pdu)
    show("vendor event 0xFF to the host", event.to_bytes())
    print(f"     {event.describe()}")

    if event.result != 0:
        print(f"\nthe peer refused, and the manager stayed in {link.sink.state.name}")
        return 0

    print("6. the DSP's side, and the session is open")
    state = link.finish_setup()
    print(f"     manager now in {state.name}")

    print("7. the host starts the stream")
    link.source.start_stream()
    show("start stream, opcode 0x38", link.transport.sent[-1])
    link.feed_all(
        Event.STREAM_READY_FROM_DSP,
        Event.STREAM_READY_FROM_CONTROLLER,
        Event.PEER_STREAM_START_RSP,
        Event.STREAM_HOST_SETUP_COMPLETE,
    )
    print(f"     manager now in {link.sink.state.name}")
    print("\nthe audio itself is not here: the link has moved to the other radio, whose packet")
    print("format is produced by hardware. docs/protocol.md section 7 says what that means.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
