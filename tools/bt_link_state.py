"""Report the real state of a Bluetooth audio device: links, transports, profile.

Written because a card profile named "headset" is NOT evidence that a voice
link exists. Mistaking the two produced a false concurrency result in this
project: an A2DP transport was acquired while the profile said headset, and the
microphone appeared to work because a whole-file average blended three live
seconds with ten dead ones. This tool prints the facts that settle it:

  - the kernel's actual ACL / eSCO / SCO handles for the device
  - every BlueZ media transport and its state
  - the PipeWire card profile
  - a verdict on whether both an audio transport and a voice link are live

  python3 tools/bt_link_state.py MAC

The kernel link list needs privileges; without them the tool says so instead of
guessing. Read-only: it changes nothing.
"""
import re
import shutil
import subprocess
import sys

MAC_RE = r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$"


def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return out.returncode, out.stdout + out.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def kernel_links(mac):
    """Parse 'hcitool con' for this device's baseband links."""
    if not shutil.which("hcitool"):
        return None, "hcitool not installed"
    rc, out = run(["hcitool", "con"])
    if rc != 0 or "Connections" not in out:
        rc2, out2 = run(["sudo", "-n", "hcitool", "con"])
        if rc2 != 0:
            return None, "needs privileges (try: sudo -n hcitool con)"
        out = out2
    links = []
    for line in out.splitlines():
        if mac.upper() not in line.upper():
            continue
        kind = "ACL" if " ACL " in line else ("eSCO" if "eSCO" in line else
                                              ("SCO" if " SCO " in line else "?"))
        handle = None
        m = re.search(r"handle (\d+)", line)
        if m:
            handle = int(m.group(1))
        links.append({
            "kind": kind,
            "handle": handle,
            "encrypted": "ENCRYPT" in line,
            "raw": line.strip(),
        })
    return links, None


def bluez_transports(mac):
    try:
        import dbus
    except ImportError:
        return None, "dbus-python not available"
    tag = "dev_" + mac.upper().replace(":", "_")
    try:
        bus = dbus.SystemBus()
        mgr = dbus.Interface(bus.get_object("org.bluez", "/"),
                             "org.freedesktop.DBus.ObjectManager")
        objs = mgr.GetManagedObjects()
    except Exception as exc:
        return None, "could not reach BlueZ: %s" % exc
    found = []
    for path, ifaces in objs.items():
        t = ifaces.get("org.bluez.MediaTransport1")
        if t is None or tag not in path:
            continue
        cfg = bytes(bytearray(t.get("Configuration", b"")))
        raw_codec = t.get("Codec", None)
        try:
            codec = int(raw_codec) if raw_codec is not None else None
        except (TypeError, ValueError):
            codec = None
        found.append({"path": path, "state": str(t.get("State", "?")),
                      "uuid": str(t.get("UUID", "")), "config": cfg,
                      "codec": codec})
    return found, None


def describe_config(cfg, codec=None):
    """Describe an A2DP codec configuration, choosing the right layout.

    The layouts differ per codec and are not interchangeable: decoding an AAC
    configuration with the SBC layout produces confident nonsense. The codec
    number carried alongside the configuration decides the layout (0 is SBC,
    2 is AAC, 255 is vendor specific). Without it, only the raw bytes are shown.
    """
    n = len(cfg)
    if codec is None:
        return "codec not reported, raw %s" % bytes(cfg).hex()
    if codec == 255:
        vendor = int.from_bytes(cfg[:4], "little")
        names = {0x4C: "Apple", 0x0A: "Qualcomm/CSR", 0x012D: "Sony", 0xD7: "Qualcomm"}
        cid = cfg[4] | cfg[5] << 8 if n >= 6 else 0
        return "%s vendor codec id 0x%04x" % (names.get(vendor, "0x%08x" % vendor), cid)
    if codec == 0:  # SBC
        freq = {0x08: 16000, 0x04: 32000, 0x02: 44100, 0x01: 48000}.get(cfg[0] >> 4, "?")
        chan = {0x08: "mono", 0x04: "dual", 0x02: "stereo",
                0x01: "joint"}.get(cfg[0] & 0x0F, "?")
        return "SBC %s Hz %s bitpool %d" % (freq, chan, cfg[3])
    if codec == 2:  # MPEG-2/4 AAC
        objs = {0x80: "MPEG-2 AAC LC", 0x40: "MPEG-4 AAC LC",
                0x20: "MPEG-4 AAC LTP", 0x10: "MPEG-4 AAC scalable"}
        obj = ", ".join(v for k, v in objs.items() if cfg[0] & k) or "?"
        # 12 frequency bits: all of byte 1, then the top nibble of byte 2.
        fb = (cfg[1] << 4) | (cfg[2] >> 4)
        table = [(0x800, 8000), (0x400, 11025), (0x200, 12000), (0x100, 16000),
                 (0x080, 22050), (0x040, 24000), (0x020, 32000), (0x010, 44100),
                 (0x008, 48000), (0x004, 64000), (0x002, 88200), (0x001, 96000)]
        freqs = [hz for bit, hz in table if fb & bit]
        chan = {0x08: "mono", 0x04: "stereo"}.get(cfg[2] & 0x0C, "?")
        rate = ((cfg[3] & 0x7F) << 16) | (cfg[4] << 8) | cfg[5]
        vbr = "VBR" if cfg[3] & 0x80 else "CBR"
        return "%s %s Hz %s %d bit/s %s" % (
            obj, "/".join(str(f) for f in freqs) or "?", chan, rate, vbr)
    return "unrecognised layout (%d bytes, codec %s)" % (n, codec)


def card_profile(mac):
    if not shutil.which("pactl"):
        return None
    rc, out = run(["pactl", "list", "cards"])
    if rc != 0:
        return None
    tag = "bluez_card." + mac.upper().replace(":", "_")
    inside = False
    for line in out.splitlines():
        if tag in line:
            inside = True
        elif inside and "Active Profile:" in line:
            return line.split(":", 1)[1].strip()
    return None


def main():
    if len(sys.argv) != 2 or not re.match(MAC_RE, sys.argv[1]):
        raise SystemExit("usage: bt_link_state.py AA:BB:CC:DD:EE:FF")
    if not sys.platform.startswith("linux"):
        raise SystemExit("bt_link_state.py reads the Linux kernel's link list, BlueZ and "
                         "PipeWire, so it needs Linux")
    mac = sys.argv[1].upper()
    print("device %s" % mac)

    prof = card_profile(mac)
    print("\ncard profile : %s" % (prof or "unknown"))
    print("  note: the profile is a host-side policy label. It does not prove")
    print("        a link of either kind is established.")

    links, err = kernel_links(mac)
    print("\nkernel baseband links:")
    if err:
        print("  unavailable: %s" % err)
    elif not links:
        print("  none (device not connected?)")
    else:
        for link in links:
            print("  %-5s handle %-4s %s" % (link["kind"], link["handle"],
                                             "encrypted" if link["encrypted"] else ""))

    transports, err2 = bluez_transports(mac)
    print("\nBlueZ media transports:")
    if err2:
        print("  unavailable: %s" % err2)
    elif not transports:
        print("  none")
    else:
        for t in transports:
            line = "  %s state=%s" % (t["path"].rsplit("/", 2)[-2:][-1], t["state"])
            if t["config"]:
                line += " cfg=%s (%s)" % (t["config"].hex(" "),
                                          describe_config(t["config"],
                                                          t.get("codec")))
            print(line)

    print("\nverdict:")
    voice = [link for link in (links or []) if link["kind"] in ("SCO", "eSCO")]
    audio = [t for t in (transports or []) if t["state"] == "active"]
    if links is None:
        print("  cannot judge: no kernel link visibility")
    else:
        print("  voice link live        : %s" % ("yes" if voice else "no"))
        print("  media transport active : %s" % ("yes" if audio else "no"))
        if voice and audio:
            print("  BOTH are live - genuine concurrency")
        elif audio:
            print("  audio only; a microphone reading now would be silence")
        elif voice:
            print("  voice only; no media stream is being carried")
        else:
            print("  neither path is currently carrying audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
