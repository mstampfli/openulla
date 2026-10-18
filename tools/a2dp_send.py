"""Feed audio to an acquired A2DP transport while another profile holds the mic.

Proves (or disproves) that the AirPods render a stereo A2DP stream while the
voice path is the active card profile. Acquires the BlueZ media transport
directly, encodes with libsbc using the configuration BlueZ negotiated, and
paces real-time A2DP media packets.

  python3 a2dp_send.py --device MAC --probe       encode-only self check, no send
  python3 a2dp_send.py --device MAC --tone 12     send a 12 s stereo test tone
  python3 a2dp_send.py --device MAC --wav FILE    send a WAV (48 kHz stereo s16)

Nothing here is destructive: the transport is released on exit.
"""
import argparse
import array
import ctypes
import ctypes.util
import math
import os
import re
import struct
import sys
import time
import wave

import dbus

TRANSPORT = "org.bluez.MediaTransport1"
MAC_RE = r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$"


def device_prefix(adapter, mac):
    """BlueZ object path prefix of a device, from its address."""
    if not re.match(MAC_RE, mac):
        raise SystemExit("not a Bluetooth address: %r" % mac)
    return "/org/bluez/%s/dev_%s" % (adapter, mac.upper().replace(":", "_"))

# libsbc constants (sbc.h)
SBC_FREQ = {16000: 0, 32000: 1, 44100: 2, 48000: 3}
SBC_BLK = {4: 0, 8: 1, 12: 2, 16: 3}
SBC_MODE_MONO, SBC_MODE_DUAL, SBC_MODE_STEREO, SBC_MODE_JOINT = 0, 1, 2, 3
SBC_AM_SNR, SBC_AM_LOUDNESS = 0, 1
SBC_SB = {4: 0, 8: 1}


class SbcT(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_ulong),
        ("frequency", ctypes.c_uint8),
        ("blocks", ctypes.c_uint8),
        ("subbands", ctypes.c_uint8),
        ("mode", ctypes.c_uint8),
        ("allocation", ctypes.c_uint8),
        ("bitpool", ctypes.c_uint8),
        ("endian", ctypes.c_uint8),
        ("priv", ctypes.c_void_p),
        ("priv_alloc_base", ctypes.c_void_p),
    ]


def load_sbc():
    name = ctypes.util.find_library("sbc") or "libsbc.so.1"
    lib = ctypes.CDLL(name)
    lib.sbc_init.argtypes = [ctypes.POINTER(SbcT), ctypes.c_ulong]
    lib.sbc_init.restype = ctypes.c_int
    lib.sbc_encode.argtypes = [ctypes.POINTER(SbcT), ctypes.c_void_p, ctypes.c_size_t,
                               ctypes.c_void_p, ctypes.c_size_t,
                               ctypes.POINTER(ctypes.c_ssize_t)]
    lib.sbc_encode.restype = ctypes.c_ssize_t
    lib.sbc_get_frame_length.argtypes = [ctypes.POINTER(SbcT)]
    lib.sbc_get_frame_length.restype = ctypes.c_size_t
    lib.sbc_get_codesize.argtypes = [ctypes.POINTER(SbcT)]
    lib.sbc_get_codesize.restype = ctypes.c_size_t
    lib.sbc_finish.argtypes = [ctypes.POINTER(SbcT)]
    return lib


def decode_config(cfg):
    """Decode a 4-byte A2DP SBC configuration into libsbc parameters."""
    freq_bits, chan_bits = cfg[0] >> 4, cfg[0] & 0x0F
    blk_bits = cfg[1] >> 4
    sb_bits = (cfg[1] >> 2) & 0x03
    alloc_bits = cfg[1] & 0x03
    bitpool = cfg[3]
    freq = {0x08: 16000, 0x04: 32000, 0x02: 44100, 0x01: 48000}[freq_bits]
    mode = {0x08: SBC_MODE_MONO, 0x04: SBC_MODE_DUAL,
            0x02: SBC_MODE_STEREO, 0x01: SBC_MODE_JOINT}[chan_bits]
    blocks = {0x08: 4, 0x04: 8, 0x02: 12, 0x01: 16}[blk_bits]
    subbands = {0x02: 4, 0x01: 8}[sb_bits]
    alloc = SBC_AM_SNR if alloc_bits == 0x02 else SBC_AM_LOUDNESS
    channels = 1 if mode == SBC_MODE_MONO else 2
    return dict(freq=freq, mode=mode, blocks=blocks, subbands=subbands,
                alloc=alloc, bitpool=bitpool, channels=channels)


def make_encoder(lib, p):
    sbc = SbcT()
    if lib.sbc_init(ctypes.byref(sbc), 0) != 0:
        raise RuntimeError("sbc_init failed")
    sbc.frequency = SBC_FREQ[p["freq"]]
    sbc.blocks = SBC_BLK[p["blocks"]]
    sbc.subbands = SBC_SB[p["subbands"]]
    sbc.mode = p["mode"]
    sbc.allocation = p["alloc"]
    sbc.bitpool = p["bitpool"]
    sbc.endian = 0  # little endian PCM
    return sbc


def find_transport(bus, prefix):
    mgr = dbus.Interface(bus.get_object("org.bluez", "/"),
                         "org.freedesktop.DBus.ObjectManager")
    for path, ifaces in mgr.GetManagedObjects().items():
        if path.startswith(prefix + "/") and TRANSPORT in ifaces:
            return path, ifaces[TRANSPORT]
    raise RuntimeError("no media transport under %s" % prefix)


def gen_tone(seconds, rate, channels):
    """A stereo sweep plus a beat, so both channels are obviously distinct."""
    n = int(seconds * rate)
    buf = array.array("h", bytes(2 * channels * n))
    for i in range(n):
        t = i / rate
        left = 0.32 * math.sin(2 * math.pi * (330 + 110 * math.sin(t * 0.7)) * t)
        right = 0.32 * math.sin(2 * math.pi * (440 + 110 * math.sin(t * 0.7)) * t)
        env = 0.55 + 0.45 * math.sin(2 * math.pi * 2.0 * t)
        buf[i * channels] = int(max(-1, min(1, left * env)) * 32000)
        if channels > 1:
            buf[i * channels + 1] = int(max(-1, min(1, right * env)) * 32000)
    return buf.tobytes()


def read_wav(path, rate, channels):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit("need 16-bit WAV")
        if w.getframerate() != rate or w.getnchannels() != channels:
            raise SystemExit("WAV is %d Hz %dch, transport wants %d Hz %dch"
                             % (w.getframerate(), w.getnchannels(), rate, channels))
        return w.readframes(w.getnframes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="device address, AA:BB:CC:DD:EE:FF")
    ap.add_argument("--adapter", default="hci0", help="BlueZ adapter (default hci0)")
    ap.add_argument("--probe", action="store_true", help="encode-only self check")
    ap.add_argument("--tone", type=float, default=0, help="seconds of test tone")
    ap.add_argument("--wav", help="48 kHz stereo 16-bit WAV to send")
    ap.add_argument("--stdin", action="store_true",
                    help="stream raw s16 stereo PCM from stdin until EOF")
    args = ap.parse_args()

    lib = load_sbc()
    bus = dbus.SystemBus()
    path, props = find_transport(bus, device_prefix(args.adapter, args.device))
    cfg = bytes(bytearray(props["Configuration"]))
    codec = int(props.get("Codec", -1))
    if codec != 0:
        raise SystemExit("transport %s carries codec %d, not SBC (0); this tool "
                         "only encodes SBC" % (path, codec))
    p = decode_config(cfg)
    print("transport %s" % path)
    print("config %s -> %d Hz, %d ch, %d blocks, %d subbands, bitpool %d"
          % (cfg.hex(" "), p["freq"], p["channels"], p["blocks"],
             p["subbands"], p["bitpool"]))

    sbc = make_encoder(lib, p)
    codesize = lib.sbc_get_codesize(ctypes.byref(sbc))
    framelen = lib.sbc_get_frame_length(ctypes.byref(sbc))
    print("encoder: codesize %d bytes pcm -> frame %d bytes" % (codesize, framelen))
    if codesize == 0 or framelen == 0:
        raise SystemExit("libsbc reported zero sizes; parameters rejected")

    pcm_per_frame_samples = p["blocks"] * p["subbands"]
    if args.probe:
        src = gen_tone(0.5, p["freq"], p["channels"])
        out = ctypes.create_string_buffer(framelen + 64)
        written = ctypes.c_ssize_t(0)
        used = lib.sbc_encode(ctypes.byref(sbc), src[:codesize], codesize,
                              out, len(out), ctypes.byref(written))
        print("probe: consumed %d pcm bytes, produced %d sbc bytes"
              % (used, written.value))
        if used != codesize or written.value != framelen:
            raise SystemExit("probe mismatch: encoder did not behave as described")
        print("probe ok (encoder works; nothing was transmitted)")
        return 0

    pcm = None
    if args.wav:
        pcm = read_wav(args.wav, p["freq"], p["channels"])
    elif args.tone > 0:
        pcm = gen_tone(args.tone, p["freq"], p["channels"])
    elif not args.stdin:
        raise SystemExit("give --probe, --tone SECONDS, --wav FILE or --stdin")

    obj = bus.get_object("org.bluez", path)
    iface = dbus.Interface(obj, TRANSPORT)
    fd_obj, read_mtu, write_mtu = iface.Acquire()
    fd = fd_obj.take()
    print("acquired: write_mtu %d" % write_mtu)

    frames_per_pkt = max(1, (write_mtu - 13) // framelen)
    pcm_per_pkt = codesize * frames_per_pkt
    samples_per_pkt = pcm_per_frame_samples * frames_per_pkt
    pkt_dur = samples_per_pkt / p["freq"]
    print("%d frames/packet, %.1f ms/packet" % (frames_per_pkt, pkt_dur * 1000))

    seq, ts, sent, errs = 0, 0, 0, 0
    out = ctypes.create_string_buffer(framelen * frames_per_pkt + 64)
    start = time.monotonic()

    def next_block():
        """Return one packet's worth of PCM, or None when the source ends."""
        if args.stdin:
            buf = b""
            while len(buf) < pcm_per_pkt:
                chunk = sys.stdin.buffer.read(pcm_per_pkt - len(buf))
                if not chunk:
                    if not buf:
                        return None
                    buf += bytes(pcm_per_pkt - len(buf))  # pad the tail
                    break
                buf += chunk
            return buf
        return None

    try:
        off = 0
        while True:
            if args.stdin:
                block = next_block()
                if block is None:
                    break
            else:
                if off + pcm_per_pkt > len(pcm):
                    break
                block = pcm[off:off + pcm_per_pkt]
            payload = b""
            for k in range(frames_per_pkt):
                chunk = block[k * codesize:(k + 1) * codesize]
                written = ctypes.c_ssize_t(0)
                used = lib.sbc_encode(ctypes.byref(sbc), chunk, len(chunk),
                                      out, len(out), ctypes.byref(written))
                if used <= 0 or written.value <= 0:
                    raise RuntimeError("encode failed at offset %d" % off)
                payload += bytes(out[:written.value])
            off += pcm_per_pkt
            # RTP header, then the one-byte SBC payload header (frame count).
            hdr = struct.pack("!BBHII", 0x80, 0x60, seq & 0xFFFF,
                              ts & 0xFFFFFFFF, 0x4C415354)
            pkt = hdr + bytes((frames_per_pkt & 0x0F,)) + payload
            try:
                os.write(fd, pkt)
                sent += 1
            except BlockingIOError:
                errs += 1
            except OSError as exc:
                print("write failed after %d packets: %s" % (sent, exc))
                break
            seq += 1
            ts += samples_per_pkt
            target = start + seq * pkt_dur
            slack = target - time.monotonic()
            if slack > 0:
                time.sleep(slack)
        print("sent %d packets (%.1f s of audio), %d would-block"
              % (sent, sent * pkt_dur, errs))
    finally:
        os.close(fd)
        try:
            iface.Release()
            print("transport released")
        except dbus.DBusException as exc:
            print("release: %s" % exc.get_dbus_name())
    return 0


if __name__ == "__main__":
    sys.exit(main())
