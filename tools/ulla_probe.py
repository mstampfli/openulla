#!/usr/bin/env python3
"""Run the firmware's own predictions against a real Bluetooth controller.

docs/protocol.md reads testable rules out of the earbud firmware, and until now they were
notes in an issue. This sends them, reads what comes back, and says what each answer means:

    sudo python3 tools/ulla_probe.py                 # against hci0
    sudo python3 tools/ulla_probe.py --adapter hci1 --handle 0x000b
    python3 tools/ulla_probe.py --dry-run            # print the bytes, send nothing

It establishes its own baseline first, because that turned out to matter: an Intel AX211
answers **0x0C to every unimplemented opcode in this group**, which is the same code a session
command answers when its gate fails [docs/measurements.md section 10]. So the tool sends an
opcode nobody implements, learns this controller's blanket refusal, and only then reads the
real answers against it. The discriminators are the event (Command Complete against Command
Status) and 0xFE55's three outcomes.

It sends nothing but the opcodes this project documents, it needs no connection for the first
two probes, and it changes no state of its own: every command here either reads something or
is refused. The raw socket needs root; without it the tool says so rather than guessing.
"""
import argparse
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import hci
from openulla.codes import HciError

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_CHANNEL_RAW = 0
HCI_FILTER = 2
EVT_CMD_COMPLETE = 0x0E
EVT_CMD_STATUS = 0x0F
UNIMPLEMENTED = hci.vendor_opcode(0x3FF)  # 0xFFFF: the baseline, nobody's command
# The positive control, and the tool is worth less without it. A negative baseline alone cannot
# tell a controller that refuses everything from a path where nothing is reaching a controller
# at all: both print the same blanket refusal on every row. This is a command in the **same**
# vendor group, so it takes the same queue, and one every Intel controller implements. If it
# comes back Command Complete with a payload while the ULLA opcodes come back refused, the
# refusals are the controller's own. If it is refused too, the run says nothing about ULLA and
# the tool says so instead of reporting a result. Non-Intel hosts: pass --positive with a
# vendor command your own controller implements, or --no-positive to accept a weaker run.
INTEL_READ_VERSION = 0xFC05
CONTROLS = ("baseline", "positive control")

# Each probe: the opcode, the bytes, what the firmware would answer, and what that would mean.
# The parameters come from openulla.hci so the tool cannot drift from the reference.
IN_RANGE = 800
OUT_OF_RANGE = 801


def probes(handle, positive=INTEL_READ_VERSION):
    """The predictions of docs/protocol.md 6.6, 6.8, 6.9 and 7.1, in the order to run them."""
    control = []
    if positive is not None:
        control = [(
            "positive control",
            positive,
            hci.command(positive),
            "a payload, proving these commands reach a controller at all",
        )]
    return [
        *control,
        (
            "baseline",
            UNIMPLEMENTED,
            hci.command(UNIMPLEMENTED),
            "nothing: this opcode is nobody's command, and its answer is the control",
        ),
        (
            "0xFE55 in range",
            0xFE55,
            hci.session_command(0xFE55, struct.pack("<i", IN_RANGE)),
            f"success, because {IN_RANGE} is inside the signed bound",
        ),
        (
            "0xFE55 out of range",
            0xFE55,
            hci.session_command(0xFE55, struct.pack("<i", OUT_OF_RANGE)),
            f"status 0x11, because {OUT_OF_RANGE} is outside it",
        ),
        (
            "0xFE53 read descriptor",
            hci.OPCODE_STREAM_DESCRIPTOR,
            hci.read_stream_descriptor(),
            "0x0C for the non-source role while a link is up, else the descriptor",
        ),
        (
            "0xFE57 stream address",
            hci.OPCODE_STREAM_DMA_ADDRESS,
            hci.stream_dma_address(0),
            "0x0C for the source role while a link is up",
        ),
        (
            "0xFE58 stream stop",
            hci.OPCODE_STREAM_STOP,
            hci.stream_stop(),
            "0x0C for the source role while a link is up",
        ),
        *packet_probes(),
    ]


def packet_probes():
    """The two commands that reach Apple's packet types, and the three other size writers.

    These are sent **with no parameters at all**, which is the point of them. A controller that
    does not implement an opcode refuses it the same way it refuses any other [section 10 of
    docs/measurements.md]; a controller that does implement one but needs arguments answers
    Invalid HCI Command Parameters instead, and refuses it before acting. So an empty request
    separates "no such command here" from "this command exists" without ever handing a
    controller a value that would change its state. Running them against commodity silicon is
    what gives docs/testing.md's advice its control: the opcodes are the short route into
    Apple's packet-type table on the earbuds, and the question for anyone else's chip is
    whether they mean anything there at all.
    """
    implemented = "0x12 (invalid parameters) if the opcode exists here, 0x0C if it does not"
    return [
        ("0xFD02 packet size", hci.OPCODE_PACKET_SIZE, hci.command(hci.OPCODE_PACKET_SIZE),
         implemented),
        ("0xFDEE packet table", hci.OPCODE_PACKET_TABLE_DIRECT,
         hci.command(hci.OPCODE_PACKET_TABLE_DIRECT), implemented),
        ("0xFD7C size halfword", hci.OPCODE_PACKET_SIZE_HALFWORD,
         hci.command(hci.OPCODE_PACKET_SIZE_HALFWORD), implemented),
        ("0xFD7D size halfword", hci.OPCODE_PACKET_SIZE_HALFWORD_ALT,
         hci.command(hci.OPCODE_PACKET_SIZE_HALFWORD_ALT), implemented),
        ("0xFD5F internal size", hci.OPCODE_PACKET_SIZE_INTERNAL,
         hci.command(hci.OPCODE_PACKET_SIZE_INTERNAL), implemented),
    ]


def open_socket(adapter):
    """A raw HCI socket on one adapter, filtered to the two events a command answers with."""
    index = int(adapter.removeprefix("hci"))
    sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    # type mask: events; event mask: command complete and command status.
    mask = struct.pack(
        "<LLLl", 1 << 4, (1 << EVT_CMD_COMPLETE) | (1 << EVT_CMD_STATUS), 0, 0
    )
    sock.setsockopt(socket.SOL_HCI if hasattr(socket, "SOL_HCI") else 0, HCI_FILTER, mask)
    sock.settimeout(3.0)
    # CPython's sockaddr_hci takes the device alone on this platform; passing the channel as a
    # second element is refused with "wrong format", and the raw channel is the default anyway.
    sock.bind((index,))
    return sock


def ask(sock, data):
    """Send one command packet and return (event, status, echoed opcode, return bytes)."""
    sock.send(b"\x01" + data)
    while True:
        packet = sock.recv(260)
        if len(packet) < 4 or packet[0] != 0x04:
            continue
        code, params = packet[1], packet[3:]
        if code == EVT_CMD_STATUS and len(params) >= 4:
            return code, params[0], int.from_bytes(params[2:4], "little"), b""
        if code == EVT_CMD_COMPLETE and len(params) >= 4:
            return code, params[3], int.from_bytes(params[1:3], "little"), params[4:]


def read(status):
    name = HciError(status).name if status in set(HciError) else "unnamed"
    return f"0x{status:02x} ({'success' if status == 0 else name})"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--adapter", default="hci0")
    ap.add_argument("--handle", default="0x000b", help="connection handle for the probes")
    ap.add_argument("--dry-run", action="store_true", help="print the bytes and send nothing")
    ap.add_argument("--positive", default=hex(INTEL_READ_VERSION),
                    help="a vendor command this controller does implement, as the control")
    ap.add_argument("--no-positive", action="store_true",
                    help="skip the positive control and accept a run that proves less")
    args = ap.parse_args()
    handle = int(args.handle, 0)

    rows = probes(handle, None if args.no_positive else int(args.positive, 0))
    if args.dry_run:
        for name, opcode, data, expected in rows:
            print(f"{name:24} 0x{opcode:04x}  {data.hex(' ')}\n{'':24} {expected}")
        return 0

    try:
        sock = open_socket(args.adapter)
    except PermissionError:
        raise SystemExit("a raw HCI socket needs root: run this under sudo") from None
    except OSError as exc:
        raise SystemExit(f"cannot open {args.adapter}: {exc}") from None

    answers = {}
    for name, opcode, data, expected in rows:
        try:
            event, status, echoed, returned = ask(sock, data)
        except TimeoutError:
            print(f"{name:24} no answer in three seconds")
            continue
        kind = "Command Complete" if event == EVT_CMD_COMPLETE else "Command Status"
        answers[name] = (event, status)
        print(f"{name:24} 0x{opcode:04x} -> {kind}, status {read(status)}")
        if returned:
            print(f"{'':24} returned {returned.hex(' ')}")
        if echoed != opcode:
            print(f"{'':24} the answer names 0x{echoed:04x}, not the command sent")
        if name not in CONTROLS:
            print(f"{'':24} a controller that implements it answers {expected}")

    print()
    control = answers.get("positive control")
    if control is not None:
        event, status = control
        if event != EVT_CMD_COMPLETE or status != 0:
            print(f"The positive control was refused ({read(status)}), so this run proves "
                  "nothing: a path that reaches no controller refuses every row the same way "
                  "a controller that implements none of them does. Give --positive a vendor "
                  "command this controller does implement and run it again.")
            return 1
        print("The positive control answered Command Complete with a payload, so these "
              "commands do reach the controller and the refusals below are its own.")
    else:
        print("No positive control was sent (--no-positive), so a blanket refusal below "
              "cannot be told apart from a path that reaches no controller.")
    base = answers.get("baseline")
    if base is None:
        print("no baseline, so nothing below can be read")
        return 1
    base_event, base_status = base
    print(f"This controller answers an unimplemented opcode with "
          f"{'Command Complete' if base_event == EVT_CMD_COMPLETE else 'Command Status'}, "
          f"status {read(base_status)}.")
    # Both controls are excluded: the baseline is what the others are compared against, and the
    # positive control is meant to differ from it, so counting either would turn the tool's own
    # instrument into a finding.
    tested = {n: v for n, v in answers.items() if n not in CONTROLS}
    same = [n for n, v in tested.items() if v == base]
    if len(same) == len(tested):
        print("Every ULLA opcode answered exactly like that, so this controller implements "
              "none of them. That is the expected result on anything but Apple silicon, and "
              "it is the control the other outcomes are read against.")
        return 0
    print("Some opcodes answered differently from the baseline, which is the interesting case:")
    for name, (event, status) in tested.items():
        if (event, status) != base:
            print(f"  {name}: "
                  f"{'Command Complete' if event == EVT_CMD_COMPLETE else 'Command Status'}, "
                  f"status {read(status)}")
    print("Report those lines, with the controller and its firmware version, in "
          "https://github.com/mstampfli/openulla/issues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
