#!/usr/bin/env python3
"""Re-derive what the documentation and the code claim, from a firmware image on this machine.

Every address in docs/protocol.md and in openulla/ is a byte offset into a payload of Apple's
own published AirPods firmware. This tool reads those offsets and checks that the image still
says what we say it says: the session manager's whole transition table, the band plan, the
command table, the PDU lengths, the dispatch tables, the reason names and the stream
endpoints. It prints one line per claim and exits non-zero if any of them fails.

No firmware is included here and none is written out; the payloads come from Apple's own
asset catalog and stay on your machine:

    tools/fetch_firmware.sh
    python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \\
        extract acib payloads/acib.bin --payload FTAB
    python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \\
        extract rkof payloads/rkof.bin --payload FTAB
    python3 tools/verify_claims.py --payloads payloads

The build these offsets were read from is 9A348. A different build moves them, which is the
point of the tool: it tells you which claims still hold.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import bands, hci, lmp  # noqa: E402
from openulla.codes import (  # noqa: E402
    FIRMWARE_NAMES,
    REASON_FIRMWARE_NAMES,
    RESULT_FIRMWARE_NAMES,
    Mode,
    RejectReason,
    Result,
)
from openulla.session import TRANSITIONS  # noqa: E402

# acib, the Bluetooth controller image
BAND_TABLE = 0xF52E8
HCI_KEYS = 0xF1928
HCI_HANDLERS = 0xF1230
HCI_KEY_COUNT = 446
HCI_KEY_MASK = 0x3FFF
# The handler addresses docs/protocol.md 6.1 and 6.4 publish, by command opcode.
COMMAND_HANDLERS = {
    0xFE50: 0x1DA40,
    0xFF77: 0x192DC,
    0xFF78: 0x19324,
    0xFF79: 0x193F4,
    0xFF7A: 0x19528,
    0xFF7C: 0x19704,
    0xFF8E: 0x19F9C,
    0xFF92: 0x1E074,
    0xFFB3: 0x18D04,
    0xFFBD: 0x1926C,
    0xFD31: 0x1E0DE,
}
ABSENT_OPCODE = 0xFF75  # no key of its own; a masked search lands on the one below
ABSENT_OPCODE_ALIAS = 0x7F75
GATE_KEY = 0xBF77  # 0xFF77's own key carries different high bits, and still matches it
SERIALIZER_LENGTHS = {  # address of the mov.w ip, #N in the per-opcode case -> the length
    0x3A79A: (lmp.OPCODE_ACCEPTED, 2),
    0x3A7B2: (lmp.OPCODE_NOT_ACCEPTED, 3),
    0x3A834: (lmp.OPCODE_ULLA_CONFIG, 7),
    0x3AA8A: (lmp.OPCODE_ULLA_SETUP, 9),
}
RECEIVE_TABLE = 0x3B254  # halfwords, index opcode-1, targets relative to 0x3b23e
RECEIVE_BASE = 0x3B23E
RECEIVE_TARGETS = {0x03: 0x3B696, 0x04: 0x3B71C, 0x73: 0x3BB1E, 0x74: 0x3B704, 0x7B: 0x3B558}
ESCAPE_7B_TABLE = 0x3BC14  # halfwords, index extended opcode - 1
ESCAPE_7B_COUNT = 39
ESCAPE_7B_STUB = 0x3C2C6  # where every unimplemented extended opcode of that space goes
ESCAPE_7B_REFUSED = {0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x12, 0x13, 0x1D, 0x1E, 0x24}
ESCAPE_7B_TARGETS = {lmp.EXT_ALLOWED_BANDS: 0x3C004, lmp.EXT_BAND_RANGES: 0x3BFC2}
BUILDER_7B_TABLE = 0x3A9BA  # halfwords, index extended opcode - 0x0A, 31 entries
BUILDER_7B_FIRST = 0x0A
BUILDER_7B_NO_BUILDER = 0x3ABF0  # the same target as out of range
# The case each extended opcode's body builder lands on, as docs/protocol.md 5.7 publishes it.
BUILDER_7B_CASES = {
    0x3ABC8: (0x0A, 0x0B),
    0x3A9F8: (0x0C, 0x0D, 0x0E, 0x17),
    0x3AAB6: (0x0F,),
    0x3A9FE: (0x10, 0x11, 0x20, 0x21),
    0x3AB8A: (0x15, 0x16, 0x24),
    0x3ABB8: (0x18, 0x27),
    0x3AC10: (0x19,),
    0x3ABFC: (0x1A,),
    0x3AC18: (0x1B,),
    0x3ABDA: (0x1C,),
    0x3ABA0: (0x1F, 0x28),
    0x3ABD0: (0x26,),
    BUILDER_7B_NO_BUILDER: (0x12, 0x13, 0x14, 0x1D, 0x1E, 0x22, 0x23, 0x25),
}
ESCAPE_7F_TABLE = 0x3B3BC
ESCAPE_7F_COUNT = 34
ESCAPE_7F_STUB = 0x3BF52
ESCAPE_7F_REFUSED = {0x08, 0x09, 0x0A, 0x0E, 0x0F, 0x12, 0x13, 0x14}
EVENT_LITERAL = 0x11D70
EVENT_LITERAL_VALUE = 0x0A0007FF

# rkof, the RTKitOS features image
TRANSITION_INDEX = 0x1BE53C
MODE_NAMES = 0x1BE190
RESULT_NAMES = 0x1BE5BC
REASON_LOW_TABLE = 0x2777C  # byte table, reasons 0..6
REASON_HIGH_TABLE = 0x2778C  # byte table, reasons 0x81..0x87
A2DP_DESCRIPTORS = (0x1C14D0, 0x1C14E8, 0x1C1500)
A2DP_CAPABILITIES = {
    0xFF: bytes.fromhex("4c00000001800080008c0083e800"),
    0x02: bytes.fromhex("c0018c83e800"),
    0x00: bytes.fromhex("3fff0235"),
}


def u8(img, off):
    return img[off]


def u16(img, off):
    return struct.unpack_from("<H", img, off)[0]


def u32(img, off):
    return struct.unpack_from("<I", img, off)[0]


def cstring(img, off, limit=128):
    end = img.find(b"\0", off, off + limit)
    return img[off : end if end >= 0 else off + limit].decode("ascii", "replace")


def check_band_table(acib, rkof):
    """The eight records of the band plan, byte for byte. [docs/protocol.md 6.3]"""
    for band in bands.BAND_TABLE:
        at = BAND_TABLE + band.index * bands.RECORD_SIZE
        got = acib[at : at + bands.RECORD_SIZE]
        if got != band.to_bytes():
            return False, f"band {band.index} at 0x{at:x} is {got.hex(' ')}"
    return True, f"8 records of {bands.RECORD_SIZE} bytes at acib 0x{BAND_TABLE:x}"


def check_transition_table(acib, rkof):
    """Every row of the session manager's table, against openulla.session. [3.4]"""
    expected = {}
    for row in TRANSITIONS:
        for event in row.events:
            expected.setdefault(int(row.state), set()).add(
                (int(event), int(row.ok), int(row.other), row.handler)
            )
    rows = 0
    for state in range(16):
        ptr, count = struct.unpack_from("<II", rkof, TRANSITION_INDEX + 8 * state)
        if ptr == 0 and count == 0:
            if state in expected:
                return False, f"state {state} has rows in the library but none in the image"
            continue
        if ptr >= len(rkof) or count > 64:
            return False, f"state {state} index entry is 0x{ptr:x}, {count} rows"
        found = set()
        for i in range(count):
            event, ok, other, handler = struct.unpack_from("<IIII", rkof, ptr + 16 * i)
            found.add((event, ok, other, handler & ~1))
        if found != expected.get(state, set()):
            missing = expected.get(state, set()) - found
            extra = found - expected.get(state, set())
            return False, f"state {state}: {len(missing)} missing, {len(extra)} unexpected"
        rows += count
    return True, f"{rows} rows over 15 states, events, both next states and handlers"


def check_hci_command_table(acib, rkof):
    """The command table, its ordering, and the handler of every command we send. [6.1, 6.4]"""
    keys = [u16(acib, HCI_KEYS + 2 * i) for i in range(HCI_KEY_COUNT)]
    masked = [k & HCI_KEY_MASK for k in keys]
    if HCI_HANDLERS + 4 * HCI_KEY_COUNT != HCI_KEYS:
        return False, "the handler table no longer ends where the key table begins"
    if not all(masked[i] < masked[i + 1] for i in range(HCI_KEY_COUNT - 1)):
        return False, "the masked keys are no longer increasing, so the binary search differs"
    for opcode, handler in sorted(COMMAND_HANDLERS.items()):
        hits = [i for i, m in enumerate(masked) if m == (opcode & HCI_KEY_MASK)]
        if len(hits) != 1:
            return False, f"0x{opcode:04x} matches {len(hits)} keys under the mask"
        got = u32(acib, HCI_HANDLERS + 4 * hits[0]) & ~1
        if got != handler:
            return False, f"0x{opcode:04x} now resolves to 0x{got:x}, not 0x{handler:x}"
    # The two aliases the documentation warns about: a masked hit is not an exact one.
    aliased = [keys[i] for i, m in enumerate(masked) if m == (ABSENT_OPCODE & HCI_KEY_MASK)]
    if aliased != [ABSENT_OPCODE_ALIAS]:
        return False, f"0x{ABSENT_OPCODE:04x} masks onto {[hex(k) for k in aliased]}"
    gate = [keys[i] for i, m in enumerate(masked) if m == (hci.OPCODE_BAND_GATE & HCI_KEY_MASK)]
    if gate != [GATE_KEY]:
        return False, f"the band gate's key is {[hex(k) for k in gate]}, not 0x{GATE_KEY:04x}"
    return True, (
        f"{len(COMMAND_HANDLERS)} handlers as published, keys ordered under the mask, "
        f"0x{ABSENT_OPCODE:04x} absent behind 0x{ABSENT_OPCODE_ALIAS:04x}"
    )


def check_pdu_lengths(acib, rkof):
    """The length each PDU declares, from the serializer's own constant. [5.1, 5.3]"""
    for at, (opcode, length) in SERIALIZER_LENGTHS.items():
        if u16(acib, at) != 0xF04F:
            return False, f"0x{at:x} is not a mov.w of an immediate"
        hw2 = u16(acib, at + 2)
        if (hw2 & 0x8F00) != 0x0C00 or (hw2 & 0xFF) != length:
            return False, f"opcode 0x{opcode:02x} at 0x{at:x} no longer declares {length}"
    return True, "accepted 2, not accepted 3, 0x74 7, 0x73 9, including the header byte"


def check_receive_dispatch(acib, rkof):
    """Where the parser sends each opcode, with the table's own alignment. [5.3]"""
    for opcode, target in RECEIVE_TARGETS.items():
        got = RECEIVE_BASE + 2 * u16(acib, RECEIVE_TABLE + 2 * (opcode - 1))
        if got != target:
            return False, f"opcode 0x{opcode:02x} now lands on 0x{got:x}, not 0x{target:x}"
    return True, f"table at acib 0x{RECEIVE_TABLE:x}, {len(RECEIVE_TARGETS)} opcodes checked"


def check_escape_dispatch(acib, rkof):
    """Both escape spaces: the two messages we encode, and what each space refuses. [5.6]"""
    for ext, target in ESCAPE_7B_TARGETS.items():
        got = ESCAPE_7B_TABLE + 2 * u16(acib, ESCAPE_7B_TABLE + 2 * (ext - 1))
        if got != target:
            return False, f"extended 0x{ext:02x} now lands on 0x{got:x}, not 0x{target:x}"
    for table, count, stub, refused in (
        (ESCAPE_7B_TABLE, ESCAPE_7B_COUNT, ESCAPE_7B_STUB, ESCAPE_7B_REFUSED),
        (ESCAPE_7F_TABLE, ESCAPE_7F_COUNT, ESCAPE_7F_STUB, ESCAPE_7F_REFUSED),
    ):
        got = {
            ext
            for ext in range(1, count + 1)
            if table + 2 * u16(acib, table + 2 * (ext - 1)) == stub
        }
        if got != refused:
            return False, (
                f"the space at 0x{table:x} refuses {sorted(hex(e) for e in got)}, "
                f"not {sorted(hex(e) for e in refused)}"
            )
    return True, (
        f"0x7B: {ESCAPE_7B_COUNT} entries, {len(ESCAPE_7B_REFUSED)} refused; "
        f"0x7F: {ESCAPE_7F_COUNT} entries, {len(ESCAPE_7F_REFUSED)} refused"
    )


def check_transmit_builders(acib, rkof):
    """Which body builder each extended opcode of the 0x7B space uses. [5.7]"""
    expected = {ext: case for case, exts in BUILDER_7B_CASES.items() for ext in exts}
    covered = 0
    for ext, case in sorted(expected.items()):
        index = ext - BUILDER_7B_FIRST
        got = BUILDER_7B_TABLE + 2 * u16(acib, BUILDER_7B_TABLE + 2 * index)
        if got != case:
            return False, f"extended 0x{ext:02x} now builds at 0x{got:x}, not 0x{case:x}"
        covered += 1
    without = sum(
        1
        for ext in range(BUILDER_7B_FIRST, BUILDER_7B_FIRST + 31)
        if BUILDER_7B_TABLE + 2 * u16(acib, BUILDER_7B_TABLE + 2 * (ext - BUILDER_7B_FIRST))
        == BUILDER_7B_NO_BUILDER
    )
    if without != len(BUILDER_7B_CASES[BUILDER_7B_NO_BUILDER]):
        return False, f"{without} extended opcodes have no builder, not 8"
    return True, f"{covered} extended opcodes, {without} of them with no builder at all"


def check_event_shape(acib, rkof):
    """The constant word that gives the event its code, length and id. [6]"""
    got = u32(acib, EVENT_LITERAL)
    if got != EVENT_LITERAL_VALUE:
        return False, f"the literal at acib 0x{EVENT_LITERAL:x} is 0x{got:08x}"
    code, length, ident = got & 0xFF, (got >> 8) & 0xFF, got >> 16
    if (code, length, ident) != (0xFF, hci.ModeChangeEvent.PARAM_LENGTH, hci.EVENT_ID_MODE_CHANGE):
        return False, f"event 0x{code:02x} length {length} id 0x{ident:04x}"
    return True, f"event 0x{code:02x}, {length} parameters, id 0x{ident:04x}"


def case_literal(img, target):
    """The string a table-branch case loads with its pc-relative ldr, or None."""
    for at in (target, target + 2):
        ins = u16(img, at)
        if (ins & 0xF800) == 0x4800:  # LDR (literal), Rt from bits 8-10
            literal = ((at + 4) & ~3) + 4 * (ins & 0xFF)
            word = u32(img, literal)
            if 0 < word < len(img):
                return cstring(img, word)
    return None


def reason_case(img, value):
    """Where the formatter's byte tables send one reason value."""
    if value < 0x80:
        table, index = REASON_LOW_TABLE, value
    else:
        table, index = REASON_HIGH_TABLE, value - 0x81
    return table + 2 * img[table + index]


def check_names(acib, rkof):
    """The mode, result and reason names, from the tables the firmware's own log indexes."""
    for mode in Mode:
        name = cstring(rkof, u32(rkof, MODE_NAMES + 4 * mode.value))
        if name != FIRMWARE_NAMES[mode]:
            return False, f"mode {mode.value} is named {name!r}"
    for result in Result:
        name = cstring(rkof, u32(rkof, RESULT_NAMES + 4 * result.value))
        if name != RESULT_FIRMWARE_NAMES[result]:
            return False, f"result {result.value} is named {name!r}"
    checked = 0
    for reason in RejectReason:
        if reason.value in (0, 0xAA, 0xFF):
            continue  # 0 is the fallthrough text, and these two are compared directly
        found = case_literal(rkof, reason_case(rkof, reason.value))
        if found != REASON_FIRMWARE_NAMES[reason]:
            return False, f"reason 0x{reason.value:02x} resolves to {found!r}"
        checked += 1
    hole = case_literal(rkof, reason_case(rkof, 2))
    if hole is None or hole.startswith("REASON_"):
        return False, f"reason 2 now resolves to {hole!r}, so 5.4 needs rewriting"
    return True, f"{len(Mode)} modes, {len(Result)} results, {checked} reasons, 2 unnamed"


def check_a2dp_endpoints(acib, rkof):
    """The three stream endpoints, each descriptor pointing at its capability. [ulla.md 4.6]"""
    seen = {}
    for at in A2DP_DESCRIPTORS:
        codec_type, length, pointer = u8(rkof, at + 0xA), u8(rkof, at + 0xB), u32(rkof, at + 0xC)
        if codec_type not in A2DP_CAPABILITIES:
            return False, f"descriptor at 0x{at:x} names codec type 0x{codec_type:02x}"
        want = A2DP_CAPABILITIES[codec_type]
        if length != len(want):
            return False, f"codec 0x{codec_type:02x} declares {length} bytes, not {len(want)}"
        got = rkof[pointer : pointer + length]
        if got != want:
            return False, f"codec 0x{codec_type:02x} at 0x{pointer:x} is {got.hex(' ')}"
        seen[codec_type] = pointer
    if len(seen) != 3:
        return False, f"only {len(seen)} distinct codec types"
    return True, "vendor 0xFF, AAC 0x02 and SBC 0x00, lengths and bytes as published"


CHECKS = (
    ("session transition table", check_transition_table),
    ("band plan", check_band_table),
    ("HCI command table", check_hci_command_table),
    ("PDU lengths", check_pdu_lengths),
    ("LMP receive dispatch", check_receive_dispatch),
    ("escape space dispatch", check_escape_dispatch),
    ("0x7B body builders", check_transmit_builders),
    ("mode-change event shape", check_event_shape),
    ("mode, result and reason names", check_names),
    ("A2DP stream endpoints", check_a2dp_endpoints),
)


def load(path, what):
    if not path or not os.path.exists(path):
        raise SystemExit(
            f"no {what} payload: pass --{what} PATH or --payloads DIR holding {what}.bin.\n"
            "The docstring of this file says how to extract it from Apple's own asset."
        )
    with open(path, "rb") as handle:
        return handle.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payloads", help="directory holding acib.bin and rkof.bin")
    ap.add_argument("--acib", help="the Bluetooth controller payload")
    ap.add_argument("--rkof", help="the RTKitOS features payload")
    args = ap.parse_args()
    acib_path = args.acib or (os.path.join(args.payloads, "acib.bin") if args.payloads else None)
    rkof_path = args.rkof or (os.path.join(args.payloads, "rkof.bin") if args.payloads else None)
    acib, rkof = load(acib_path, "acib"), load(rkof_path, "rkof")

    print(f"acib {len(acib)} bytes, rkof {len(rkof)} bytes")
    failed = 0
    for name, check in CHECKS:
        try:
            ok, detail = check(acib, rkof)
        except Exception as exc:  # a claim that cannot even be read is a failed claim
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<32} {detail}")
        failed += 0 if ok else 1
    print(f"{len(CHECKS) - failed} of {len(CHECKS)} claims hold")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
