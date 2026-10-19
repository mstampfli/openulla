#!/usr/bin/env python3
"""Probe Apple's accessory protocol (AAP) service 4 on an AirPods L2CAP channel.

Every frame layout, opcode and class this sends lives in `openulla/aap.py`, beside the
firmware address that proves it; this tool is the part that leaves the machine. Read-only by
default: it opens the service and prints what comes back. Commands are sent with --seq, a
comma list run in order on one channel, or with the --start-session shorthand:

  start[:MODE]       ask the earbud to start a ULLA session with this host (changes its state)
  subscribe[:CLASS]  subscribe to a report class, by default the ULLA one
  stream-start       ask it to start the audio stream
  stream-stop        ask it to stop the audio stream
  stop               ask it to stop the session

Closing the channel resets the earbud's session state.

--simulate runs the same sequence against openulla.sink, which answers the way the earbud
answers wherever that is proven, so the byte stream can be read without hardware. The
earbud's own internal events are not invented there, so a start command is answered by
silence rather than by the status reply a real pair sends once its app and its sibling have
got there; tools/ulla_demo.py is the one that walks a whole session, by feeding those events
in explicitly. See docs/implementation.md.
"""
import argparse
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import aap
from openulla.errors import ProtocolError
from openulla.sim import DEFAULT_PEER, SinkTransport
from openulla.sink import Sink
from openulla.transport import L2capTransport

MAC_RE = r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$"
NAMES = {
    aap.FrameType.OPEN: "open",
    aap.FrameType.OPEN_REPLY: "open-reply",
    aap.FrameType.CONTROL_2: "type2",
    aap.FrameType.CONTROL_3: "type3",
    aap.FrameType.DATA: "data",
}


def describe(data):
    if len(data) < aap.HEADER_LENGTH:
        return "short frame %s" % data.hex(" ")
    frame = aap.parse_frame(data)
    out = "type %d (%s) service %d" % (frame.ftype, NAMES.get(frame.ftype, "?"), frame.service)
    if frame.ftype == aap.FrameType.OPEN_REPLY and len(frame.body) >= 6:
        reply = aap.OpenReply.from_bytes(frame.body.ljust(aap.OPEN_REPLY_BODY_LENGTH, b"\0"))
        out += " status 0x%02x pad 0x%02x versions %d,%d rest %s" % (
            reply.status, frame.body[1], reply.major, reply.minor, frame.body[6:].hex(" "))
    elif frame.ftype == aap.FrameType.DATA and len(frame.body) >= 2:
        opcode, payload = aap.parse_payload(frame.body)
        out += " opcode 0x%04x payload %s" % (opcode, payload.hex(" "))
    else:
        out += " body %s" % frame.body.hex(" ")
    return out


def step_frame(step, version_pair):
    """One command frame from a --seq word, or a SystemExit for a word we do not know."""
    name, _, arg = step.partition(":")
    if name == "start":
        mode = int(arg, 0) if arg else 0
        return aap.start_session(mode), "start ULLA session, mode %d" % mode
    if name == "subscribe":
        cls = int(arg, 0) if arg else aap.CLASS_ULLA_REPORTS
        frame = aap.subscribe_reports(cls, aap.class_width(version_pair))
        return frame, "subscribe to report class 0x%06x" % cls
    if name == "stream-start":
        return aap.start_stream(), "start the audio stream"
    if name == "stream-stop":
        return aap.stop_stream(), "stop the audio stream"
    if name == "stop":
        return aap.stop_session(int(arg, 0) if arg else 0), "stop ULLA session"
    raise SystemExit("unknown step %r" % step)


def drain(transport, seconds, label):
    """Print every frame that arrives within the window."""
    end = time.monotonic() + seconds
    got = 0
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        try:
            data = transport.recv(left)
        except TimeoutError:
            break
        except OSError as exc:
            print("  %s: recv failed: %s" % (label, exc))
            break
        except ProtocolError as exc:
            print("  %s: %s" % (label, exc))
            break
        if not data:
            print("  %s: channel closed by the earbud" % label)
            break
        got += 1
        print("  %s <- %s" % (label, describe(data)))
        print("      raw %s" % data.hex(" "))
    if not got:
        print("  %s: nothing within %gs" % (label, seconds))
    return got


def predictions():
    """The mode bytes to try, and what each answer would mean. [protocol.md 2.4, 6, 5.9]

    The control runs first for the same reason tools/ulla_probe.py sends an opcode nobody
    implements first: without it, silence cannot be told from refusal.
    """
    return [
        (0, "the control: the documented path, which a real pair answers with status 0"),
        (2, "the lite mode has a name and a value in the firmware; whether a host may ask "
            "for it is not established"),
        (7, "nothing validates this byte and neither name table bounds its index, so the "
            "prediction is that it is accepted exactly like 0"),
    ]


def open_channel(args, wait, ver_a, ver_b):
    """One fresh channel with the service open on it, because a start needs an idle manager.

    Closing the channel resets the earbud's session state [protocol.md 2.4, measurements 3],
    so each prediction gets its own channel or the second start is ignored in silence and
    reads as a refusal when it is only a busy manager.
    """
    if args.simulate:
        transport = SinkTransport(Sink(), DEFAULT_PEER)
    else:
        transport = L2capTransport.connect(args.device)
    transport.send(aap.open_request(aap.SERVICE_ULLA, ver_a, ver_b))
    drain(transport, wait, "open")
    return transport


def run_predictions(args, wait, ver_a, ver_b):
    """Try each mode byte on its own channel and say what the answers mean together."""
    answers = {}
    for mode, meaning in predictions():
        print("\n== mode %d: %s" % (mode, meaning))
        try:
            transport = open_channel(args, wait, ver_a, ver_b)
        except OSError as exc:
            print("  connect failed: %s" % exc)
            return 1
        command = aap.start_session(mode)
        print("  -> start ULLA session, mode %d: %s" % (mode, command.hex(" ")))
        transport.send(command)
        got = drain(transport, wait if args.simulate else max(wait, 4.0), "mode%d" % mode)
        answers[mode] = got
        transport.close()

    control = answers.get(0, 0)
    print("\nthe control answered with %d frame(s)." % control)
    if not control:
        print("Nothing came back for mode 0 either, so this run says nothing about the other "
              "two: either no pair is connected, or the earbud's own app and sibling had not "
              "got there yet. Against --simulate that is expected, because the simulator does "
              "not raise the earbud's internal events; tools/ulla_demo.py is the one that "
              "walks a whole session.")
        return 0
    for mode, _ in predictions()[1:]:
        if answers[mode] == control:
            print("  mode %d was answered exactly like the control, so it is accepted." % mode)
        elif answers[mode] == 0:
            print("  mode %d got nothing while the control was answered: that byte IS "
                  "validated somewhere this project has not found." % mode)
        else:
            print("  mode %d answered differently from the control, which is the interesting "
                  "case: report the frames above." % mode)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", help="AirPods address, AA:BB:CC:DD:EE:FF")
    ap.add_argument("--simulate", action="store_true",
                    help="run against openulla.sink instead of a real device")
    ap.add_argument("--version", default="1,2",
                    help="version words in the open request (default 1,2)")
    ap.add_argument("--wait", type=float, default=2.0, help="seconds to listen after each send")
    ap.add_argument("--start-session", action="store_true",
                    help="also send the ULLA start-session command (changes earbud state)")
    ap.add_argument("--mode", type=lambda s: int(s, 0), default=0,
                    help="mode byte for --start-session (default 0)")
    ap.add_argument("--seq", help="comma list of commands to send in one channel: "
                                  "start, start:MODE, subscribe, subscribe:CLASS, "
                                  "stream-start, stream-stop or stop")
    ap.add_argument("--predict", action="store_true",
                    help="run the mode-byte predictions, each on its own channel, control "
                         "first (changes the earbud's state and resets it again)")
    args = ap.parse_args()
    if not args.simulate:
        if not args.device:
            raise SystemExit("--device is required unless --simulate is given")
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise SystemExit("this Python has no Bluetooth sockets (socket.AF_BLUETOOTH); "
                             "aap_probe.py needs Linux with BlueZ")
        if not re.match(MAC_RE, args.device):
            raise SystemExit("not a Bluetooth address: %r" % args.device)
    ver_a, ver_b = (int(x, 0) for x in args.version.split(","))
    # The simulator answers inside the send, so the only wait it needs is enough for the
    # drain loop to take what is already queued.
    wait = 0.05 if args.simulate else args.wait

    if args.predict:
        where = "openulla.sink" if args.simulate else args.device
        print("running the mode-byte predictions against %s, each on its own channel" % where)
        return run_predictions(args, wait, ver_a, ver_b)

    if args.simulate:
        transport = SinkTransport(Sink(), DEFAULT_PEER)
        print("simulating a session against openulla.sink, PSM 0x%04x" % aap.PSM)
    else:
        print("connecting to %s PSM 0x%04x" % (args.device, aap.PSM))
        try:
            transport = L2capTransport.connect(args.device)
        except OSError as exc:
            raise SystemExit("connect failed: %s" % exc) from exc
        print("connected")

    req = aap.open_request(aap.SERVICE_ULLA, ver_a, ver_b)
    print("-> open service %d versions %d,%d: %s" % (aap.SERVICE_ULLA, ver_a, ver_b, req.hex(" ")))
    transport.send(req)
    drain(transport, wait, "open")

    steps = []
    if args.start_session:
        steps.append("start:%d" % args.mode)
    if args.seq:
        steps += [s.strip() for s in args.seq.split(",") if s.strip()]
    version_pair = (ver_a << 16) | ver_b
    for n, step in enumerate(steps, 1):
        cmd, label = step_frame(step, version_pair)
        print("-> step %d: %s: %s" % (n, label, cmd.hex(" ")))
        transport.send(cmd)
        drain(transport, wait if args.simulate else max(wait, 4.0), "step%d" % n)

    transport.close()
    print("closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
