"""List radio-related parameter names in the AirPods RF tuning map (TMAP payload, NSKeyedArchiver plist)."""
import sys
import struct
import plistlib

TERMS = ("hdr", "bdr", "le2m", "le1m", "unii", "nb_", "ulla", "5g", "6g", "band", "link_", "tx_power", "cca", "ant", "hop")

b = open(sys.argv[1], "rb").read()
phoff, phlen = struct.unpack_from(">2I", b, 36)
off = phoff
payloads = []
while off < phoff + phlen:
    plen, tag = struct.unpack_from(">I4s", b, off)
    poff, plength = struct.unpack_from(">2I", b, off + 32)
    payloads.append((tag.decode(), poff, plength))
    off += plen

tmap = [p for p in payloads if p[0] == "TMAP"][-1]
_, o, l = tmap
d = plistlib.loads(b[o:o + l])
objs = d["$objects"]
strs = [x for x in objs if isinstance(x, str)]
radio = sorted({s for s in strs if any(t in s.lower() for t in TERMS)})
print(f"TMAP: {len(objs)} objects, {len(strs)} strings, {len(radio)} radio-related names")


def stem(s):
    return "".join("N" if c.isdigit() else c for c in s)


groups = {}
for s in radio:
    groups.setdefault(stem(s), []).append(s)
for k in sorted(groups):
    print(f"  {k}  (x{len(groups[k])})")
