"""Probe Apple's accessory protocol (AAP) service 4 on an AirPods L2CAP channel.

Every constant below cites the AirPods firmware (build 9A348, rkof payload, offsets into
rkof.bin) that proves it. Nothing here is guessed.

  PSM 0x1001                   rkof 0x40e78: 0x5c1e4(0x1001, handler)
  frame [u16 type][u16 service] rkof 0x4052c: two little-endian u16 reads (0x625d2 with the
                                le flag set, 0x625f4..0x62606)
  type 0 open / 1 reply / 4 data rkof 0x405f6: tbb over type 0..4
  service id 4                  rkof 0x8af9e: u16 4 in the registration params
  open body, 16 bytes total     rkof 0x4073a..0x4078a: u16, u16, then 8 bytes; the handler
                                rejects len <= 0xf (rkof 0x40600)
  service 4 payload [u16 opcode] rkof 0x6cef4 -> 0x308e0
  opcode 0x36 = start ULLA session, one mode byte
                                rkof switch table 0x6cf4c case 0x36 -> 0x6d602, byte read
                                0x308c8, then 0x27a58 posts ULLA_MGR_EVT_SESSION_START_REQ

Read-only by default: it opens the service and prints what comes back. --start-session also
sends the ULLA start command, which asks the earbud to begin a session with this host.
"""
import argparse
import re
import socket
import struct
import sys
import time

PSM = 0x1001
SERVICE_AAP = 4
TYPE_OPEN, TYPE_OPEN_REPLY, TYPE_DATA = 0, 1, 4
OPCODE_START_SESSION = 0x36
# rkof switch table 0x6cf4c case 0x3c -> 0x6d396: reads four bytes, then 0x27ac8 posts
# ULLA_MGR_EVT_SESSION_STOP_REQ_FROM_PEER.
OPCODE_STOP_SESSION = 0x3C
# Opcode 0x17 is the wrapper used in both directions (rkof 0x6cefa handles it before the
# opcode switch; rkof 0x6e198 builds it). Body: [u32 class][u16 len][len bytes] when the
# session's negotiated version pair is above 0x10001 (rkof 0x6cf1a), which is the case for the
# open we send. The class is dispatched at rkof 0x6d07e; class 0x00200000 reaches 0x6d116 ->
# 0x27f60, which registers the report workers for notification ids 0xe910 and 0xe911
# (0x27f6e..0x27f7e), tells the other earbud over UTP (0x27f9a) and sends its own controller
# HCI OCF 0x375 (0x27fb0). 0x27f60 never reads the body, so any length works; four bytes are
# sent so the firmware's allocate-and-read path gets a non-zero length.
OPCODE_WRAPPER = 0x17
CLASS_ULLA_REPORTS = 0x00200000
MAC_RE = r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$"


def frame(ftype, service, body=b""):
    return struct.pack("<HH", ftype, service) + body


def open_request(service, ver_a, ver_b):
    """16-byte open request: header, two version words, eight bytes."""
    return frame(TYPE_OPEN, service, struct.pack("<HH", ver_a, ver_b) + bytes(8))


def describe(data):
    if len(data) < 4:
        return "short frame %s" % data.hex(" ")
    ftype, service = struct.unpack_from("<HH", data, 0)
    names = {TYPE_OPEN: "open", TYPE_OPEN_REPLY: "open-reply", 2: "type2", 3: "type3",
             TYPE_DATA: "data"}
    out = "type %d (%s) service %d" % (ftype, names.get(ftype, "?"), service)
    body = data[4:]
    if ftype == TYPE_OPEN_REPLY and len(body) >= 6:
        # rkof 0x40884 + 0x3f778: status, pad, then the accessory's version words.
        status, pad, v1, v2 = struct.unpack_from("<BBHH", body, 0)
        out += " status 0x%02x pad 0x%02x versions %d,%d rest %s" % (
            status, pad, v1, v2, body[6:].hex(" "))
    elif ftype == TYPE_DATA and len(body) >= 2:
        out += " opcode 0x%04x payload %s" % (struct.unpack_from("<H", body, 0)[0],
                                              body[2:].hex(" "))
    else:
        out += " body %s" % body.hex(" ")
    return out


def drain(sock, seconds, label):
    """Print every frame that arrives within the window."""
    end = time.monotonic() + seconds
    got = 0
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        sock.settimeout(left)
        try:
            data = sock.recv(1024)
        except socket.timeout:
            break
        except OSError as exc:
            print("  %s: recv failed: %s" % (label, exc))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="AirPods address, AA:BB:CC:DD:EE:FF")
    ap.add_argument("--version", default="1,2",
                    help="version words in the open request (default 1,2)")
    ap.add_argument("--wait", type=float, default=2.0, help="seconds to listen after each send")
    ap.add_argument("--start-session", action="store_true",
                    help="also send the ULLA start-session command (changes earbud state)")
    ap.add_argument("--mode", type=lambda s: int(s, 0), default=0,
                    help="mode byte for --start-session (default 0)")
    ap.add_argument("--seq", help="comma list of commands to send in one channel: "
                                  "start, start:MODE or stop")
    args = ap.parse_args()
    if not re.match(MAC_RE, args.device):
        raise SystemExit("not a Bluetooth address: %r" % args.device)
    ver_a, ver_b = (int(x, 0) for x in args.version.split(","))

    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    print("connecting to %s PSM 0x%04x" % (args.device, PSM))
    try:
        sock.connect((args.device, PSM))
    except OSError as exc:
        raise SystemExit("connect failed: %s" % exc)
    print("connected")

    req = open_request(SERVICE_AAP, ver_a, ver_b)
    print("-> open service %d versions %d,%d: %s" % (SERVICE_AAP, ver_a, ver_b, req.hex(" ")))
    sock.send(req)
    drain(sock, args.wait, "open")

    steps = []
    if args.start_session:
        steps.append("start:%d" % args.mode)
    if args.seq:
        steps += [s.strip() for s in args.seq.split(",") if s.strip()]
    for n, step in enumerate(steps, 1):
        name, _, arg = step.partition(":")
        if name == "start":
            mode = int(arg, 0) if arg else 0
            cmd = frame(TYPE_DATA, SERVICE_AAP,
                        struct.pack("<HB", OPCODE_START_SESSION, mode))
            label = "start ULLA session, mode %d" % mode
        elif name == "subscribe":
            cls = int(arg, 0) if arg else CLASS_ULLA_REPORTS
            body = struct.pack("<IH", cls, 4) + bytes(4)
            cmd = frame(TYPE_DATA, SERVICE_AAP, struct.pack("<H", OPCODE_WRAPPER) + body)
            label = "subscribe to report class 0x%06x" % cls
        elif name == "stop":
            cmd = frame(TYPE_DATA, SERVICE_AAP,
                        struct.pack("<HI", OPCODE_STOP_SESSION, int(arg, 0) if arg else 0))
            label = "stop ULLA session"
        else:
            raise SystemExit("unknown step %r" % step)
        print("-> step %d: %s: %s" % (n, label, cmd.hex(" ")))
        sock.send(cmd)
        drain(sock, max(args.wait, 4.0), "step%d" % n)

    sock.close()
    print("closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
