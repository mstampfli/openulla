#!/usr/bin/env python3
"""Answer like an AirPods earbud on a real L2CAP channel, so a source can be tested.

This is the other half of the protocol: openulla/sink.py answers the accessory protocol the
way the earbud's firmware does wherever that is proven, and this listens on PSM 0x1001 for
anything that wants to talk to it. Useful for exercising another implementation of the source
side over real Bluetooth, with no earbuds involved.

    python3 tools/ulla_sink.py                 # listen on PSM 0x1001
    python3 tools/ulla_sink.py --no-advance    # stay passive: raise no internal events

By default it supplies the internal events a real pair raises by itself, in the order
docs/protocol.md 3.4 gives, so a source gets its status reply and the session opens. Those
events are the simulator's, not the protocol's; --no-advance turns them off and leaves the
machine where the host's own frames put it.

An unprivileged user can bind PSM 0x1001; the kernel only restricts PSMs below 0x1000. What
this cannot do is the rest of a real earbud: no sibling, no radio, no audio.
"""
import argparse
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import aap  # noqa: E402
from openulla.describe import describe_aap  # noqa: E402
from openulla.errors import ProtocolError  # noqa: E402
from openulla.serve import Service  # noqa: E402
from openulla.sink import Sink  # noqa: E402

ANY_ADAPTER = "00:00:00:00:00:00"


def show(direction, data):
    print(f"  {direction} {data.hex(' ')}")
    try:
        for line in describe_aap(data):
            print(f"      {line}")
    except ProtocolError as exc:
        print(f"      not an AAP frame: {exc}")


def serve_one(channel, address, advance):
    peer = bytes(int(part, 16) for part in reversed(address.split(":")))
    service = Service(sink=Sink(), peer=peer, advance=advance, log=print)
    print(f"connected: {address}, session state {service.sink.state.name}")
    while True:
        data = channel.recv(1024)
        if not data:
            break
        show("<-", data)
        for reply in service.handle(data):
            show("->", reply)
            channel.send(reply)
    service.closed()
    print(f"closed: {address}, session state {service.sink.state.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=ANY_ADAPTER, help="local adapter address")
    ap.add_argument("--psm", type=lambda s: int(s, 0), default=aap.PSM)
    ap.add_argument("--no-advance", dest="advance", action="store_false",
                    help="raise none of the internal events a real pair would")
    args = ap.parse_args()
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise SystemExit("this Python has no Bluetooth sockets; ulla_sink.py needs Linux")

    listener = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    try:
        listener.bind((args.adapter, args.psm))
    except OSError as exc:
        raise SystemExit(f"cannot listen on PSM 0x{args.psm:04x}: {exc}")
    listener.listen(1)
    print(f"listening on PSM 0x{args.psm:04x}, answering as an earbud"
          + ("" if args.advance else ", passively"))
    try:
        while True:
            channel, peer = listener.accept()
            try:
                serve_one(channel, peer[0], args.advance)
            except OSError as exc:
                print(f"channel error: {exc}")
            finally:
                channel.close()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
