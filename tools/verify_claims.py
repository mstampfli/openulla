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
    python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \\
        extract sbd1 payloads/sbd1.bin --payload FTAB
    python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \\
        payload CTAB payloads/ctab.bin
    python3 tools/verify_claims.py --payloads payloads

The build these offsets were read from is 9A348. A different build moves them, which is the
point of the tool: it tells you which claims still hold.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import a2dp, bands, hci, lmp
from openulla.codes import (
    FIRMWARE_NAMES,
    REASON_FIRMWARE_NAMES,
    RESULT_FIRMWARE_NAMES,
    Mode,
    RejectReason,
    Result,
)
from openulla.procedure import (
    HANDLER_ADDRESSES,
    SINK_HANDLER_ADDRESSES,
    THIRD_HANDLER_ADDRESSES,
)
from openulla.session import TRANSITIONS

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
    0xFDF3: 0x16B20,
    0xFF8C: 0x19964,  # the two that read the state-2 request block, 6.7
    0xFE74: 0x1D492,
}
FEATURE_WORD = 0x10003290  # the controller's own feature bits, 5.8
FEATURE_KEEP_MASK = 0xA9  # what 0xFDF3 leaves alone
FEATURE_ADD_GROUP = 0x56  # what it adds, bit 6 among them
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
BUILDER_7F_TABLE = 0x3A7CC  # halfwords, index extended opcode - 3, 38 entries
BUILDER_7F_FIRST = 0x03
BUILDER_7F_NO_BUILDER = 0x3AB7E
BUILDER_7F_CASES = {
    0x3A818: (0x17, 0x18, 0x1B, 0x1C, 0x1D, 0x21, 0x22),
    0x3A9F8: (0x06, 0x0B, 0x1E, 0x1F, 0x20, 0x28),
    0x3AAB6: (0x0D, 0x27),
    0x3AABC: (0x19, 0x1A),
    0x3AAC6: (0x15, 0x16),
    0x3AADA: (0x03, 0x04),
    0x3AAEE: (0x0C, 0x26),
    0x3AB1E: (0x11,),
    0x3AB2E: (0x05,),
    0x3AB5C: (0x07,),
    0x3AB6A: (0x10,),
    BUILDER_7F_NO_BUILDER: (0x08, 0x09, 0x0A, 0x0E, 0x0F, 0x12, 0x13, 0x14, 0x23, 0x24, 0x25),
}
ESCAPE_7F_TABLE = 0x3B3BC
ESCAPE_7F_COUNT = 34
ESCAPE_7F_STUB = 0x3BF52
ESCAPE_7F_REFUSED = {0x08, 0x09, 0x0A, 0x0E, 0x0F, 0x12, 0x13, 0x14}
# The two standard arms that close every road to an extra ACL rate. [5.12]
PACKET_TABLE_EXT = 0x0B  # the packet-type-table request, how EDR's packet set was switched in
PACKET_TABLE_ARM = 0x3BE50
PACKET_TABLE_READ = 0x3BE6C  # ldrb r0, [r5], the single payload byte
PACKET_TABLE_CMP = 0x3BE70  # cmp r0, #1: table 0 or table 1, and nothing else
PACKET_TABLE_ERROR_AT = 0x3BF80  # movs r0, #0x1e, Invalid LMP Parameters
PACKET_TABLE_ERROR = 0x1E
FEATURES_EXT = 0x03  # the extended features request
FEATURES_ARM = 0x3BD68
FEATURES_HANDLER = 0x276FC
FEATURES_CALL = 0x3BD6C  # the arm's second call, which is the handler
FEATURES_PAGE_CMP = 0x2770A  # cmp r5, #3: pages 0, 1 and 2 exist
FEATURES_FILL_LEN = 0x27710  # movs r1, #8: eight zero bytes for any page above them
FEATURES_REPLY_OPCODE = 0x2777C  # movs r1, #0x7f, the specification's own escape
FEATURES_REPLY_EXT = 0x2777E  # movs r2, #4, its extended features response
FEATURES_PAGE_0 = 0x10003288
FEATURES_PAGE_12 = 0x100033A4  # page 1, then page 2 in the eight bytes after it
FEATURES_PAGE_0_LOAD = 0x27750
FEATURES_PAGE_12_LOAD = 0x27764
EVENT_LITERAL = 0x11D70
EVENT_LITERAL_VALUE = 0x0A0007FF
PROCEDURE_TABLE = 0xF2664  # the nine state handlers of 5.9
PROCEDURE_LITERAL = 0x682FC  # where the dispatcher loads that address from
GETTER_TABLE = 0xF7D60  # 534 property getters, one-based, 6.6
GETTER_TABLE_LITERAL = 0xE42C4
GETTERS = {
    0x63: 0xDC9C0,  # 0x73's 32-bit field: the hardware read with retries, 5.1
    0xA6: 0xDF9E8,  # the escape module's per-type byte, written, 6.6
    0xA7: 0xE089C,  # and the mapped byte it reads back through the link record, 6.6
    0xB9: 0xE0AC4,  # the three queries of 5.11
    0x1AF: 0xDC35C,  # the six ULLA session properties of 6.6
    0x1B0: 0xDC388,
    0x1B1: 0xDC198,
    0x1B2: 0xDC1B4,
    0x1B3: 0xDC200,
    0x1AB: 0xDC168,
}
# The numbers that are not property ids: they ride in property 0x20D's input block and reach a
# second dispatcher of 54 entries [5.2, 6.6]. Each row: the id, its arm, what the arm calls.
# The per-link record array those two properties and 0x74's own value share [5.2, 6.6]
LINK_RECORDS = 0x10005B58
LINK_RECORD_STRIDE = 0x13C
LINK_RECORD_READS = ((0xC1F2E, 0xE08A0),)  # the two mov.w of the stride, 0x74's and 0xA7's
INNER_PROPERTY = 0x20D
INNER_PROPERTY_FN = 0xE42D8  # what the table holds for it, and it just calls the dispatcher
INNER_DISPATCH = 0xDE8D0
INNER_BOUND_AT = 0xDE8E6  # cmp r5, #0x35
INNER_BOUND = 0x35
INNER_TABLE = 0xDE8F2
INNER_IDS = {
    0x02: (0xDEA46, 0xC9DDC),  # 0xFE55's word, 6.6
    0x12: (0xDEAB2, 0xC1F10),  # 0x74's 32-bit value, 5.2
    0x13: (0xDE99A, 0xC1F6C),  # 0x74's halfword, 5.2
    0x19: (0xDEA30, 0xDA8B4),  # 0xFE52 on a live link, the other role
    0x1A: (0xDEB0A, 0xDA904),  # 0xFE58 on a live link
    0x25: (0xDE972, 0xDB6A8),  # 0xFE52 on a live link, the source
    0x2B: (0xDE96A, 0xDB9AC),  # 0xFE53 on a live link
}
# What those functions do, as 7.1 publishes it, each at the instruction that shows it.
LIVE_LINK_ADDRESS_STORE = (0xDA94C, 0xEC)  # str.w r0, [r4, #0xec] in property 0x1A4
LIVE_LINK_ASSERT_LITERAL = (0xDA900, 0xF8FA7)  # the ADMA string the start loads when it is zero
LIVE_LINK_STATISTICS = ((0xDA8E2, 1), (0xDA928, 0))  # arm on start, disarm on stop
STATISTICS_PHASE = 0xC281C  # the phase setter both of them call
LIVE_LINK_DESCRIPTOR = (0xDB9C6, 0xF0)  # movs r0, #0xf0 -> the descriptor's +8
FE55_BOUND = (0xC9DDC, 0x321, 0x640)  # value - 0x321 against ~0x640: the signed +/- 800 range
# The transmit entry the encoder fills, and the one field of it that is not on the air [5.7]
ENTRY_FIELDS = (
    (0x3AC90, 0x2003, "3 at +0"),
    (0x3AC98, 0x8028, "store of that 3"),
    (0x3ACA0, 0x280A, "test of +4 against 0xa"),
    (0x3AC4A, 0x3822, "escape opcode range, from 0x22"),
    (0x3AC4C, 0x2803, "and its span of four"),
)
ENTRY_STACKED = (0x3ACA4, 0xF885, 0xB009)  # strb.w fp, [r5, #9]
# The link-up half of the five gated session commands [6.6]
GATE = 0x1DFE0  # the "is the link already up" gate each of them calls first
ROLE_TEST = 0x61AB4  # the module is on and this end is the source
GATED_COMMANDS = {  # opcode: where it calls the gate, where it calls the role test
    0xFE52: (0x1DD8C, 0x1DD92),
    0xFE53: (0x1DE66, 0x1DE6C),
    0xFE55: (0x1DDE8, 0x1DDEE),
    0xFE57: (0x1D908, 0x1D90E),
    0xFE58: (0x1D956, 0x1D95C),
}
LINK_UP_PROPERTIES = (  # the ids those paths use, where each is loaded
    (0x66B6C, 0x25),
    (0x63294, 0x19),
    (0x6329E, 0x1A),
    (0x1DE78, 0x2B),
    (0x1D91C, 0x1A4),
)
LINK_UP_REFUSALS = (  # status site, its halfword, opcode site, the opcode it answers for
    (0x1DEB0, 0x210C, 0x1DEB2, 0xFE53),
    (0x1D93A, 0x220C, 0x1D948, 0xFE57),
    (0x1D984, 0x210C, 0x1D986, 0xFE58),
)
# sbd1, the DSP payload: the ULLA node of section 7, which is AArch64 rather than Thumb
NODE_VERSION_READ = (0x9D334, 21, 0x2BC)  # ldr w8, [x21, #0x2bc]: the node's version field
NODE_VERSION_NAMES = ((0x9D338, 0x10CD42, "ULLA 1.0"), (0x9D33C, 0x10CD39, "ULLA 1.1"))
NODE_VERSION_TEST = (0x9D348, 0x11)  # cmp w8, #0x11, the only version it treats as 1.1
NODE_PROPERTY_IDS = ((0x9D0DC, 0x258), (0x9D0E4, 0x25A), (0x9D0EC, 0x259))
NODE_PROPERTY_VERSION = (0x9D0F4, 0, 0x2BC)  # id 0x259 reads that same field
NODE_PROCESSOR_NAME = (0x9E070, 0x10CD7F, "processBufferUllav11")
NODE_PROCESSOR_ERROR = (0x9D4B4, 0x02E7, 0x9D4B8, 0xE000)  # movz then movk: 0xe00002e7
NODE_CHANNEL_HEADER = (0x9D64C, 0x3C08, 0x9D650, 0x0001)  # the word each channel object gets
# What asks for a type 1 report: a queued message, and the two places its id is built [2.6]
REQUEST_CLASS_BASE = (0x5A018, 0x3000)  # sub.w r0, sl, #0x3000, so the index is the class - 0x30
REQUEST_CLASS_BOUND = (0x5A024, 0x2D5E)  # cmp r5, #0x5e
REQUEST_BRANCH = 0x5A030  # tbh [pc, r5, lsl #1]
REQUEST_TABLE = 0x5A034
REQUEST_INDEX = 0x5C  # so the class is 0x8C, and this is the arm that reports
REQUEST_ARM = (0x5A0FA, 0x5544C)  # mov r0, r4 then the call that picks the producer
REQUEST_ID = 0x8C00
REQUEST_ID_SITES = (0x84BB8, 0xE0F40)  # the zero-initialiser and the poster
REQUEST_STAGING = (0xD4864, 0x22F0, 0xD4880, 0x1002AA78)  # 240 bytes into that buffer first
# The producer of report type 1, in the controller rather than in rkof [2.6]
PRODUCER = 0x121D2  # held in the module descriptor at acib 0xf2288
PRODUCER_POINTER = 0xF2288
PRODUCER_CONSTANT = (0x121F0, 0xBBFF)  # movw r0, #0xbbff -> the message's +8
PRODUCER_LENGTH = (0x121FA, 0xBD)  # movs r0, #0xbd -> its +2, four header bytes and 185 more
PRODUCER_ID_BASE = (0x121FE, 0xE90D)  # movw r0, #0xe90d, and +3 is this report's own id
PRODUCER_COPIES = (  # add.w rd, rn, #imm for each block copy, then its size
    (0x1229C, 0x45),  # the first destination
    (0x1224E, 0x39),  # and its source
    (0x122A4, 0x85),  # the second destination
    (0x122A8, 0xB9),  # and its source
)
PRODUCER_COPY_SIZES = (0x12254, 0x122AC)  # movs r2, #0x80 at both, which is the overrun
PRODUCER_COPY_SIZE = 0x80
# The other producer: a generic sender that stamps type 0's id and copies the caller's words.
SENDER = 0x11E24
SENDER_MARKER = (0x11E3C, 0xFF)  # movs r1, #0xff -> the message's +8, as 2.6 describes
SENDER_ID = (0x11E44, 0x0A08)  # movw r1, #0xa08 -> its +0xa
SENDER_LENGTH = (0x11E30, 0x3C)  # movs r1, #0x3c -> four header bytes and 56 of payload
# What the top of type 0's path passes: the object, two word counts and three offsets.
SENDER_OBJECT = (0xC2AFC, 0x10029964)
SENDER_COUNTS = ((0xC2AB2, 0x2109), (0xC2AB4, 0x2304))  # movs r1, #9 and movs r3, #4
SENDER_OFFSETS = ((0xC2AAE, 0x44), (0xC2AB8, 0x20))  # add.w r2, r5, #0x44 and r0, r5, #0x20
SENDER_HALFWORD = (0xC2AAA, 0xF8B5, 0x0056)  # ldrh.w r0, [r5, #0x56], the last word of the 56
# Type 0's id is stamped in exactly one place in the whole image, which is the positive form of
# "only this sender produces it" [2.6]. The encoding is Thumb-2 MOVW of 0x0A08 into any low
# register: first halfword 0xF640, second 0x2008 with the register in bits 8 to 11.
SENDER_ID_ENCODING = (0xF640, 0x2008)
SCHEDULER_BASE_LOAD = 0x3DBA6  # movw r0, #0xfcd0: the id the index counts from, 5.11
SCHEDULER_BASE_ID = 0xFCD0
SCHEDULER_BOUND_AT = 0x3DBAC  # cmp r0, #0x58
SCHEDULER_BOUND = 0x58
SCHEDULER_BRANCH = 0x3DBB2  # tbh [pc, r0, lsl #1]
SCHEDULER_TABLE = 0x3DBB6  # its halfword table, one entry per id
SCHEDULER_ARMS = 40  # distinct arms over the 0x59 entries
SCHEDULER_SENDERS = {0x19: 0x293D0, 0x1A: 0x296A0}  # 0x1A is the one that sends 0x7B/0x10
MOV_R0_R8 = 0x4640  # what every one of those arms starts with
SINK_TABLE = 0xF2628  # the sink's eight, 5.10
SINK_LITERAL = 0x67370
THIRD_TABLE = 0xF2648  # the third role's seven, 5.10
THIRD_LITERAL = 0x67BFC

# rkof, the RTKitOS features image
TRANSITION_INDEX = 0x1BE53C
MODE_NAMES = 0x1BE190
RESULT_NAMES = 0x1BE5BC
PAST_MODE_NAMES = "ULLA_MGR_EVENT_UNKNOWN"  # what sits after the three mode names
MODE_NAME_LOAD = 0x2781C  # the formatter's literal for the mode list [5.9, section 6]
RESULT_NAME_LOAD = 0x27820  # and for the result list
REASON_LOW_TABLE = 0x2777C  # byte table, reasons 0..6
REASON_HIGH_TABLE = 0x2778C  # byte table, reasons 0x81..0x87
# The report path of 2.6: one listener per type, each with its own length and builder entry.
REPORT_SUBSCRIBE = 0x27F60  # where subscribing registers the three
REPORT_LISTENERS = {  # report type: (notification id, data length, listener, builder entry)
    0: (0x0A08, 0x38, 0x27CB0, 0x27F42),
    1: (0xE910, 0xB9, 0x27CDC, 0x27F38),
    2: (0xE911, 0x3D, 0x27D08, 0x27E84),
}
REPORT_SIZE_CHECKS = {0x0A08: 0x258F2, 0xE910: 0x25BB2, 0xE911: 0x25BF4}  # id, then size, in
REPORT_BUILDER = 0x27E90  # the one builder all three entries tail-call
A2DP_DESCRIPTORS = (0x1C14D0, 0x1C14E8, 0x1C1500)
# The bytes themselves live in openulla/a2dp.py, because tools/bt_link_state.py compares the
# same three against what the device advertises on the air.
A2DP_CAPABILITIES = {codec: capability for codec, (_, capability) in a2dp.ENDPOINTS.items()}

# ctab, the whole firmware table again, in one unframed LZ stream [docs/ulla.md 4.1]
CTAB_MAGIC = b"rkosftab"
CTAB_MAGIC_AT = 0x3D  # where the magic, 0x20 bytes into the table itself, lands in the stream
CTAB_MANIFEST_HEAD = 0x600  # the whole manifest's literals fall inside this much of it
CTAB_FRAMING = (b"bvx-", b"bvx1", b"bvx2", b"bvxn", b"bvx$")  # libcompression's block magics
# The FTAB entries whose offset and size survive in that head as three-byte little-endian
# literals, in the table's own order. 4.1 says why the other entries do not: a value the table
# has already used is a back-reference rather than a literal.
CTAB_MANIFEST = (
    ("aopf", 0x0020F30, 0x091B20),
    ("rtpf", 0x00B2A50, 0x031260),
    ("acib", 0x00E3CB0, 0x1044E0),
    ("phyb", 0x01E8190, 0x012200),
    ("rkof", 0x036078C, 0x1FDD80),
    ("ls00", 0x055F014, 0x017AFE),
    ("ls01", 0x0576B14, 0x017378),
    ("ls02", 0x058DE8C, 0x01E2C0),
    ("ls05", 0x05B8478, 0x00ACAC),
    ("ls06", 0x05C3124, 0x01E150),
    ("ls07", 0x05E1274, 0x02D5D0),
    ("ls08", 0x060E844, 0x02D974),
    ("ls09", 0x063C1B8, 0x02E740),
    ("ls10", 0x066A8F8, 0x02EE2C),
    ("ls16", 0x0699724, 0x0081E6),
    ("dstn", 0x070469C, 0x00EA54),
    ("Ldn0", 0x07130F0, 0x002000),
    ("Ldo2", 0x073A950, 0x000020),
    ("Lpn3", 0x074D470, 0x013760),
    ("Lpo2", 0x0763030, 0x00E5E0),
)


def u8(img, off):
    return img[off]


def u16(img, off):
    return struct.unpack_from("<H", img, off)[0]


def u32(img, off):
    return struct.unpack_from("<I", img, off)[0]


def movw(img, off):
    """The 16-bit immediate of a Thumb-2 MOVW, decoded from its two halfwords (imm4:i:imm3:imm8)."""
    first, second = u16(img, off), u16(img, off + 2)
    if first & 0xFBF0 != 0xF240:
        return None
    return (
        ((first & 0xF) << 12)
        | (((first >> 10) & 1) << 11)
        | (((second >> 12) & 7) << 8)
        | (second & 0xFF)
    )


def thumb_expand(value):
    """ThumbExpandImm of a 12-bit i:imm3:imm8 field, which is how the T3 forms carry a constant.

    Below 0x100 the field is the byte itself; the next three cases repeat that byte through the
    word; above that it is a byte with bit 7 set, rotated right by the top five bits.
    """
    byte = value & 0xFF
    if value < 0x400:
        return (byte, byte << 16 | byte, byte << 24 | byte << 8, byte * 0x01010101)[
            (value >> 8) & 3
        ]
    rotation = value >> 7
    unrotated = 0x80 | (value & 0x7F)
    return ((unrotated >> rotation) | (unrotated << (32 - rotation))) & 0xFFFFFFFF


def mov_imm(img, off):
    """The immediate a Thumb MOV carries, either the 16-bit form or the wide T2 one."""
    first = u16(img, off)
    if first & 0xF800 == 0x2000:
        return first & 0xFF
    second = u16(img, off + 2)
    if first & 0xFBEF != 0xF04F:
        return None
    field = (((first >> 10) & 1) << 11) | (((second >> 12) & 7) << 8) | (second & 0xFF)
    return thumb_expand(field)


def wide_imm(img, off):
    """The immediate of a wide ADD, SUB or MVN: all carry i:imm3:imm8, some expanded, some not.

    The T3 forms put a modified immediate constant in that field; the T4 forms (ADDW, SUBW)
    take the twelve bits as they stand.
    """
    first, second = u16(img, off), u16(img, off + 2)
    field = (((first >> 10) & 1) << 11) | (((second >> 12) & 7) << 8) | (second & 0xFF)
    if first & 0xFBE0 in (0xF100, 0xF1A0) or first & 0xFBEF == 0xF06F:
        return thumb_expand(field)
    if first & 0xFBF0 in (0xF200, 0xF2A0):
        return field
    return None


def wide_branch(img, off, link=True):
    """Where a Thumb-2 BL or B.W goes: S:I1:I2:imm10:imm11, doubled, from off + 4.

    The two encodings differ in one bit of the second halfword, bit 14: set for the call.
    """
    first, second = u16(img, off), u16(img, off + 2)
    if first & 0xF800 != 0xF000 or second & 0x9000 != 0x9000:
        return None
    if bool(second & 0x4000) != link:
        return None
    sign = (first >> 10) & 1
    first_j = 1 - (((second >> 13) & 1) ^ sign)
    second_j = 1 - (((second >> 11) & 1) ^ sign)
    value = (
        (sign << 24)
        | (first_j << 23)
        | (second_j << 22)
        | ((first & 0x3FF) << 12)
        | ((second & 0x7FF) << 1)
    )
    return off + 4 + (value - (1 << 25) if sign else value)


def a64_mov_imm(img, off):
    """The 16-bit immediate of an AArch64 MOVZ or MOVK, with the halfword it lands in."""
    word = u32(img, off)
    if word & 0x7F800000 not in (0x52800000, 0x72800000):
        return None
    return (word >> 5) & 0xFFFF, (word >> 21) & 3


def a64_cmp_imm(img, off):
    """The 12-bit immediate of an AArch64 CMP (immediate), which is SUBS into the zero register."""
    word = u32(img, off)
    if word & 0x7F800000 != 0x71000000 or word & 0x1F != 0x1F:
        return None
    return (word >> 10) & 0xFFF


def a64_ldr_offset(img, off):
    """The byte offset of an AArch64 LDR (immediate, unsigned) of a 32-bit register."""
    word = u32(img, off)
    if word & 0xFFC00000 != 0xB9400000:
        return None
    return ((word >> 10) & 0xFFF) * 4


def a64_page_target(img, off, within=4):
    """Where an ADRP points, once the ADD that completes it is found.

    The ADD does not have to be the next instruction: two of these pairs are interleaved at
    the version site, so the matching ADD is the first one that adds to the ADRP's own
    register.
    """
    word = u32(img, off)
    if word & 0x9F000000 != 0x90000000:
        return None
    register = word & 0x1F
    value = ((word >> 29) & 3) | (((word >> 5) & 0x7FFFF) << 2)
    if value & (1 << 20):
        value -= 1 << 21
    page = (off & ~0xFFF) + (value << 12)
    for step in range(1, within + 1):
        following = u32(img, off + 4 * step)
        if (
            following & 0xFF800000 == 0x91000000
            and following & 0x1F == register
            and (following >> 5) & 0x1F == register
        ):
            return page + ((following >> 10) & 0xFFF)
    return None


def cstring(img, off, limit=128):
    end = img.find(b"\0", off, off + limit)
    return img[off : end if end >= 0 else off + limit].decode("ascii", "replace")


def check_band_table(acib, rkof, ctab, sbd1):
    """The eight records of the band plan, byte for byte. [docs/protocol.md 6.3]"""
    for band in bands.BAND_TABLE:
        at = BAND_TABLE + band.index * bands.RECORD_SIZE
        got = acib[at : at + bands.RECORD_SIZE]
        if got != band.to_bytes():
            return False, f"band {band.index} at 0x{at:x} is {got.hex(' ')}"
    return True, f"8 records of {bands.RECORD_SIZE} bytes at acib 0x{BAND_TABLE:x}"


def check_transition_table(acib, rkof, ctab, sbd1):
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


def check_hci_command_table(acib, rkof, ctab, sbd1):
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


def check_pdu_lengths(acib, rkof, ctab, sbd1):
    """The length each PDU declares, from the serializer's own constant. [5.1, 5.3]"""
    for at, (opcode, length) in SERIALIZER_LENGTHS.items():
        if u16(acib, at) != 0xF04F:
            return False, f"0x{at:x} is not a mov.w of an immediate"
        hw2 = u16(acib, at + 2)
        if (hw2 & 0x8F00) != 0x0C00 or (hw2 & 0xFF) != length:
            return False, f"opcode 0x{opcode:02x} at 0x{at:x} no longer declares {length}"
    return True, "accepted 2, not accepted 3, 0x74 7, 0x73 9, including the header byte"


def check_receive_dispatch(acib, rkof, ctab, sbd1):
    """Where the parser sends each opcode, with the table's own alignment. [5.3]"""
    for opcode, target in RECEIVE_TARGETS.items():
        got = RECEIVE_BASE + 2 * u16(acib, RECEIVE_TABLE + 2 * (opcode - 1))
        if got != target:
            return False, f"opcode 0x{opcode:02x} now lands on 0x{got:x}, not 0x{target:x}"
    return True, f"table at acib 0x{RECEIVE_TABLE:x}, {len(RECEIVE_TARGETS)} opcodes checked"


def check_escape_dispatch(acib, rkof, ctab, sbd1):
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


def check_transmit_builders(acib, rkof, ctab, sbd1):
    """Which body builder each extended opcode of each space uses. [5.7]"""
    covered, without = 0, 0
    for name, table, first, count, cases, stub in (
        ("0x7B", BUILDER_7B_TABLE, BUILDER_7B_FIRST, 31, BUILDER_7B_CASES, BUILDER_7B_NO_BUILDER),
        ("0x7F", BUILDER_7F_TABLE, BUILDER_7F_FIRST, 38, BUILDER_7F_CASES, BUILDER_7F_NO_BUILDER),
    ):
        expected = {ext: case for case, exts in cases.items() for ext in exts}
        for ext, case in sorted(expected.items()):
            got = table + 2 * u16(acib, table + 2 * (ext - first))
            if got != case:
                return False, f"{name}/0x{ext:02x} now builds at 0x{got:x}, not 0x{case:x}"
            covered += 1
        stubbed = sum(
            1
            for ext in range(first, first + count)
            if table + 2 * u16(acib, table + 2 * (ext - first)) == stub
        )
        if stubbed != len(cases[stub]):
            return False, f"{name} has {stubbed} extended opcodes with no builder"
        without += stubbed
    for at, halfword, what in ENTRY_FIELDS:
        if u16(acib, at) != halfword:
            return False, f"the entry's {what} at 0x{at:x} is now 0x{u16(acib, at):04x}"
    if (u16(acib, ENTRY_STACKED[0]), u16(acib, ENTRY_STACKED[0] + 2)) != ENTRY_STACKED[1:]:
        return False, f"0x{ENTRY_STACKED[0]:x} no longer stores the sender's value at +9"
    return True, (
        f"{covered} extended opcodes over both spaces, {without} with no builder, "
        "the entry's own fields as published"
    )


def check_event_shape(acib, rkof, ctab, sbd1):
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


def literal_word(img, at):
    """The word a `ldr rN, [pc, #imm]` at this address loads, or None if that is not one."""
    ins = u16(img, at)
    if (ins & 0xF800) != 0x4800:
        return None
    return u32(img, ((at + 4) & ~3) + 4 * (ins & 0xFF))


def reason_case(img, value):
    """Where the formatter's byte tables send one reason value."""
    if value < 0x80:
        table, index = REASON_LOW_TABLE, value
    else:
        table, index = REASON_HIGH_TABLE, value - 0x81
    return table + 2 * img[table + index]


def check_names(acib, rkof, ctab, sbd1):
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


def check_dma_descriptor(acib, rkof, ctab, sbd1):
    """The three bitfield extractions of 7.2, read as instructions. [7.2]"""
    for where, expect, what in (
        (0xDABF8, (3, 3), "the buffer count at bits 3 to 5"),
        (0xDAC2C, (0x10, 4), "the flag nibble at bits 16 to 19"),
        (0xDAC50, (6, 10), "the length at bits 6 to 15"),
    ):
        # ubfx rD, rS, #lsb, #width, T1: the second halfword holds imm3, Rd, imm2 and width-1,
        # and the lsb is imm3:imm2.
        second = u16(acib, where + 2)
        lsb = (((second >> 12) & 0x7) << 2) | ((second >> 6) & 0x3)
        width = (second & 0x1F) + 1
        if (lsb, width) != expect:
            return False, f"{what} is now bits {lsb} to {lsb + width - 1}"
    if u32(acib, 0xDAC94) != 0x3FFF8 or u32(acib, 0xDAC98) != 0x52980000:
        return False, "the channel register's mask or aperture changed"
    if u32(acib, 0x88404) != 0x521C01F0:
        return False, "the descriptor is no longer read from 0x521c01f0"
    # The two address windows the firmware's own pointer check accepts (7.2).
    if u32(acib, 0x793FC) != 0x10030000 or u32(acib, 0x79400) != 0x521BFFFF:
        return False, "the two accepted address windows changed"
    return True, (
        "count at 3+3, length at 6+10, flags at 16+4, descriptor at 0x521c01f0, "
        "windows to 0x1002ffff and 0x521cffff"
    )


def check_getter_table(acib, rkof, ctab, sbd1):
    """How a number becomes a function: the 534-entry table, and the 54-entry one behind 0x20D.

    Both halves are here because telling them apart is the correction 5.2 records: a number
    passed in r0 is a table id, a number passed in an input block is an id of the dispatcher
    that property 0x20D calls.
    """
    if u32(acib, GETTER_TABLE_LITERAL) != GETTER_TABLE:
        return False, f"the dispatcher no longer loads 0x{GETTER_TABLE:x} from its literal"
    for ident, getter in sorted(GETTERS.items()):
        entry = u32(acib, GETTER_TABLE + 4 * (ident - 1))
        if entry & 0x7FFFFFFE != getter:
            return False, (
                f"id 0x{ident:x} now resolves to 0x{entry & 0x7FFFFFFE:x}, not 0x{getter:x}"
            )
    for pair in LINK_RECORD_READS:
        for at in pair:
            if mov_imm(acib, at) != LINK_RECORD_STRIDE:
                return False, f"0x{at:x} no longer strides the link records by 0x13c"
    entry = u32(acib, GETTER_TABLE + 4 * (INNER_PROPERTY - 1))
    if entry & 0x7FFFFFFE != INNER_PROPERTY_FN:
        return False, f"property 0x{INNER_PROPERTY:x} resolves to 0x{entry & 0x7FFFFFFE:x}"
    if not any(
        wide_branch(acib, INNER_PROPERTY_FN + 2 * step) == INNER_DISPATCH for step in range(8)
    ):
        return False, f"0x{INNER_PROPERTY_FN:x} no longer calls 0x{INNER_DISPATCH:x}"
    bound = u16(acib, INNER_BOUND_AT)
    if bound != 0x2D00 | INNER_BOUND:
        return False, f"the inner bound at 0x{INNER_BOUND_AT:x} is 0x{bound:04x}"
    for ident, (arm, target) in sorted(INNER_IDS.items()):
        at = INNER_TABLE + 2 * u16(acib, INNER_TABLE + 2 * ident)
        if at != arm:
            return False, f"inner id 0x{ident:x} now branches to 0x{at:x}, not 0x{arm:x}"
        if not any(
            wide_branch(acib, arm + 2 * step, link=link) == target
            for step in range(4)
            for link in (True, False)
        ):
            return False, f"inner id 0x{ident:x}'s arm no longer reaches 0x{target:x}"
    return True, (
        f"{len(GETTERS)} ids in the table at acib 0x{GETTER_TABLE:x}, "
        f"{len(INNER_IDS)} behind property 0x{INNER_PROPERTY:x}"
    )


def check_session_commands(acib, rkof, ctab, sbd1):
    """The eleven commands of 6.6, each resolving to the handler we publish for it."""
    keys = [u16(acib, HCI_KEYS + 2 * i) & HCI_KEY_MASK for i in range(HCI_KEY_COUNT)]
    for opcode, handler in sorted(hci.ULLA_SESSION_COMMANDS.items()):
        hits = [i for i, k in enumerate(keys) if k == (opcode & HCI_KEY_MASK)]
        if len(hits) != 1:
            return False, f"0x{opcode:04x} matches {len(hits)} keys under the mask"
        got = u32(acib, HCI_HANDLERS + 4 * hits[0]) & ~1
        if got != handler:
            return False, f"0x{opcode:04x} now resolves to 0x{got:x}, not 0x{handler:x}"
    # 0xFE16's bound, read as instructions: cmp r5,#0x7d then movw r0,#0x2710.
    low, high = hci.FE16_VALUE_RANGE
    if u16(acib, 0x1EC00) & 0xFF != low:
        return False, f"0xFE16's lower bound is now 0x{u16(acib, 0x1EC00) & 0xFF:x}, not 0x{low:x}"
    movw = u32(acib, 0x1EC04)
    got = ((movw >> 16) & 0xFF) | ((movw >> 20) & 0x700) | ((movw & 0x400) << 1) | (
        (movw & 0xF) << 12
    )
    if got != high:
        return False, f"0xFE16's upper bound is now 0x{got:x}, not 0x{high:x}"
    return True, (
        f"{len(hci.ULLA_SESSION_COMMANDS)} handlers as published, 0xFE16 bounded to "
        f"{low} to {high}"
    )


def check_feature_bit_commands(acib, rkof, ctab, sbd1):
    """That the two commands really write the feature byte, and with which masks. [5.8]

    Read as instructions rather than as prose: 0xFF77's handler must clear bit 5 and add
    0x20, and 0xFDF3's must mask to 0xA9 and add 0x56, both on the byte at the feature word.
    """
    for name, where, expect in (
        ("0xFF77", 0x192FC, (0xDF, 0x20)),  # and r1,#0xdf then addne r1,#0x20
        ("0xFDF3", 0x16B28, (FEATURE_KEEP_MASK, FEATURE_ADD_GROUP)),
    ):
        # "and rX, rX, #imm" as T1: 0xf00X 0xZZYY with the immediate in the low halfword
        kept = u16(acib, where + 2) & 0xFF
        added = u16(acib, where + 6) & 0xFF
        if (kept, added) != expect:
            return False, (
                f"{name} now masks with 0x{kept:02x} and adds 0x{added:02x}, "
                f"not 0x{expect[0]:02x} and 0x{expect[1]:02x}"
            )
    if u32(acib, 0x19320) != FEATURE_WORD or u32(acib, 0x16B34) != FEATURE_WORD:
        return False, "one of the two handlers no longer loads the feature word's address"
    if FEATURE_KEEP_MASK | FEATURE_ADD_GROUP != 0xFF or FEATURE_KEEP_MASK & FEATURE_ADD_GROUP:
        return False, "the two groups are no longer complementary"
    return True, (
        f"0xFF77 sets bit 5, 0xFDF3 adds 0x{FEATURE_ADD_GROUP:02x} over 0x"
        f"{FEATURE_KEEP_MASK:02x}, both at 0x{FEATURE_WORD:08x}"
    )


def check_procedure_tables(acib, rkof, ctab, sbd1):
    """The three procedures' state handlers, and the dispatchers that index them. [5.9, 5.10]"""
    for name, table, literal, addresses in (
        ("sink", SINK_TABLE, SINK_LITERAL, list(SINK_HANDLER_ADDRESSES.values())),
        ("third", THIRD_TABLE, THIRD_LITERAL, list(THIRD_HANDLER_ADDRESSES.values())),
        ("source", PROCEDURE_TABLE, PROCEDURE_LITERAL, list(HANDLER_ADDRESSES.values())),
    ):
        if u32(acib, literal) != table:
            return False, (
                f"the {name} dispatcher's literal at 0x{literal:x} is now "
                f"0x{u32(acib, literal):x}, not 0x{table:x}"
            )
        for state, address in enumerate(addresses):
            got = u32(acib, table + 4 * state)
            if got != address | 1:
                return False, (
                    f"{name} state {state} now runs 0x{got & ~1:x}, not 0x{address:x}"
                )
    # The three tables are one run of words, in that order, and the run ends after the last.
    if SINK_TABLE + 4 * len(SINK_HANDLER_ADDRESSES) != THIRD_TABLE:
        return False, "the sink's table no longer ends where the third's begins"
    if THIRD_TABLE + 4 * len(THIRD_HANDLER_ADDRESSES) != PROCEDURE_TABLE:
        return False, "the third table no longer ends where the source's begins"
    after = u32(acib, PROCEDURE_TABLE + 4 * len(HANDLER_ADDRESSES))
    if after != 0:
        return False, f"the run does not end after the source's nine entries: 0x{after:x}"
    return True, (
        f"{len(SINK_HANDLER_ADDRESSES)} sink, {len(THIRD_HANDLER_ADDRESSES)} third and "
        f"{len(HANDLER_ADDRESSES)} source states, acib 0x{SINK_TABLE:x} to 0x{PROCEDURE_TABLE:x}"
    )


def check_name_table_bounds(acib, rkof, ctab, sbd1):
    """That the mode and result lists are as long as we say, and nothing guards them. [6]

    Stated as what the image must contain, not as the absence of something: a run of zeroes
    would satisfy "no fourth mode name" and tell us nothing.
    """
    past_modes = cstring(rkof, u32(rkof, MODE_NAMES + 4 * len(Mode)))
    if past_modes != PAST_MODE_NAMES:
        return False, f"the word after the mode list now names {past_modes!r}"
    last_result = cstring(rkof, u32(rkof, RESULT_NAMES + 4 * (len(Result) - 1)))
    if last_result != RESULT_FIRMWARE_NAMES[Result.LSTO_ERROR]:
        return False, f"the last result name is now {last_result!r}"
    if u32(rkof, RESULT_NAMES + 4 * len(Result)) != 0:
        return False, "the result list does not end after five names"
    for literal, loads in ((MODE_NAMES, MODE_NAME_LOAD), (RESULT_NAMES, RESULT_NAME_LOAD)):
        if u32(rkof, loads) != literal:
            return False, f"the formatter no longer loads 0x{literal:x} from 0x{loads:x}"
    return True, f"{len(Mode)} modes then {past_modes!r}, {len(Result)} results then a null"


def check_report_listeners(acib, rkof, ctab, sbd1):
    """One listener per report type, each with its own length and builder entry. [2.6]

    Read as instructions, because every number here is an immediate: the length the listener
    asks for, the type its entry stamps, and the length the dispatcher declares for the id.
    """
    for report_type, (notification, length, listener, entry) in REPORT_LISTENERS.items():
        asked = u16(rkof, listener + 4)
        if asked != 0x2100 | length:
            return False, f"listener 0x{listener:x} asks for 0x{asked:04x}, not {length} bytes"
        pool = u32(rkof, listener + 0x24)
        if pool != entry | 1:
            return False, f"listener 0x{listener:x} hands it to 0x{pool:x}, not 0x{entry:x}"
        if (u16(rkof, entry), u16(rkof, entry + 2)) != (0x8802, 0x6841):
            return False, f"entry 0x{entry:x} no longer reads the descriptor's length and data"
        stamped = u16(rkof, entry + 4)
        if stamped != 0x2000 | report_type:
            return False, f"entry 0x{entry:x} stamps 0x{stamped:04x}, not type {report_type}"
        goes = wide_branch(rkof, entry + 6, link=False)
        if goes != REPORT_BUILDER:
            return False, f"entry 0x{entry:x} goes to {goes and hex(goes)}, not the builder"
        at = REPORT_SIZE_CHECKS[notification]
        if movw(rkof, at) != notification:
            return False, f"0x{at:x} no longer names notification 0x{notification:x}"
        declared = u16(rkof, at + 4)
        if declared != 0x2300 | length:
            return False, f"the dispatcher declares 0x{declared:04x} for 0x{notification:x}"
    registered = {u32(rkof, REPORT_SUBSCRIBE + off) for off in (0x68, 0x6C)}
    for report_type in (1, 2):
        if REPORT_LISTENERS[report_type][2] | 1 not in registered:
            return False, f"subscribing no longer registers type {report_type}'s listener"
    return True, (
        f"{len(REPORT_LISTENERS)} types: "
        + ", ".join(
            f"0x{notification:04x} {length} bytes"
            for notification, length, _, _ in REPORT_LISTENERS.values()
        )
    )


def check_session_command_gates(acib, rkof, ctab, sbd1):
    """The link-up half of the five gated session commands: gate, role test, ids, refusals. [6.6]

    Every one of them asks the same two questions in the same order, and what it does next is
    a property id or a refusal, so all three are read out of the instructions here.
    """
    for opcode, (gate_at, role_at) in GATED_COMMANDS.items():
        goes = wide_branch(acib, gate_at)
        if goes != GATE:
            return False, f"0x{opcode:04x} calls {goes and hex(goes)} first, not the gate"
        goes = wide_branch(acib, role_at)
        if goes != ROLE_TEST:
            return False, f"0x{opcode:04x} does not ask the role test at 0x{role_at:x}"
    for at, want in LINK_UP_PROPERTIES:
        got = mov_imm(acib, at)
        if got != want:
            return False, f"0x{at:x} loads {got and hex(got)}, not property 0x{want:x}"
    for status_at, status, opcode_at, opcode in LINK_UP_REFUSALS:
        if u16(acib, status_at) != status:
            return False, f"the refusal at 0x{status_at:x} is 0x{u16(acib, status_at):04x}"
        if movw(acib, opcode_at) != opcode:
            return False, f"0x{opcode_at:x} no longer answers for 0x{opcode:04x}"
    # And what those paths do, which is the stream again with other ids [7.1].
    at, offset = LIVE_LINK_ADDRESS_STORE
    if (u16(acib, at), u16(acib, at + 2)) != (0xF8C4, offset):
        return False, f"0x{at:x} no longer stores the address at +0x{offset:x}"
    at, literal = LIVE_LINK_ASSERT_LITERAL
    if u32(acib, at) != literal:
        return False, f"the start's assert string is now 0x{u32(acib, at):x}"
    for at, value in LIVE_LINK_STATISTICS:
        if u16(acib, at - 2) != 0x2000 | value:
            return False, f"0x{at - 2:x} no longer passes {value} to the statistics"
        if wide_branch(acib, at) != STATISTICS_PHASE:
            return False, f"0x{at:x} no longer calls 0x{STATISTICS_PHASE:x}"
    at, value = LIVE_LINK_DESCRIPTOR
    if u16(acib, at) != 0x2000 | value:
        return False, f"the descriptor's word at 0x{at:x} is 0x{u16(acib, at):04x}"
    at, low, span = FE55_BOUND
    if wide_imm(acib, at) != low or wide_imm(acib, at + 4) != span:
        return False, f"0xFE55's bound at 0x{at:x} is no longer {low} against {span}"
    if span != 2 * (low - 1):
        return False, f"0x{span:x} is not twice 0x{low - 1:x}, so the range is not symmetric"
    return True, (
        f"{len(GATED_COMMANDS)} commands through one gate, "
        f"{len(LINK_UP_PROPERTIES)} ids on the link-up paths, "
        f"{len(LINK_UP_REFUSALS)} role refusals of 0x0C, "
        f"0xFE55 bounded to +/-{low - 1}"
    )


def check_dsp_ulla_node(acib, rkof, ctab, sbd1):
    """The DSP's ULLA node: its version field, its property ids and its frame processor. [7]

    Section 7 is the part of this document that reads a different architecture, so these are
    AArch64 instructions: a register-offset load, two page-relative string references, three
    compares, and the two constants the processor builds with a MOVZ and a MOVK.
    """
    at, register, offset = NODE_VERSION_READ
    if a64_ldr_offset(sbd1, at) != offset or (u32(sbd1, at) >> 5) & 0x1F != register:
        return False, f"0x{at:x} no longer reads the version field at +0x{offset:x}"
    for at, address, text in NODE_VERSION_NAMES:
        if a64_page_target(sbd1, at) != address:
            return False, f"0x{at:x} no longer points at 0x{address:x}"
        if cstring(sbd1, address) != text:
            return False, f"0x{address:x} no longer reads {text!r}"
    at, version = NODE_VERSION_TEST
    if a64_cmp_imm(sbd1, at) != version:
        return False, f"the version test at 0x{at:x} is not against 0x{version:x}"
    for at, ident in NODE_PROPERTY_IDS:
        if a64_cmp_imm(sbd1, at) != ident:
            return False, f"0x{at:x} no longer answers id 0x{ident:x}"
    at, register, offset = NODE_PROPERTY_VERSION
    if a64_ldr_offset(sbd1, at) != offset or (u32(sbd1, at) >> 5) & 0x1F != register:
        return False, f"id 0x259 no longer reads +0x{offset:x} at 0x{at:x}"
    at, address, text = NODE_PROCESSOR_NAME
    if a64_page_target(sbd1, at) != address or cstring(sbd1, address) != text:
        return False, f"0x{at:x} no longer names {text!r}"
    for at, low, then, high in (NODE_PROCESSOR_ERROR, NODE_CHANNEL_HEADER):
        if a64_mov_imm(sbd1, at) != (low, 0) or a64_mov_imm(sbd1, then) != (high, 1):
            return False, f"the constant at 0x{at:x} is no longer 0x{high:04x}{low:04x}"
    return True, (
        f"the node's version at +0x{NODE_VERSION_READ[2]:x}, "
        f"{len(NODE_PROPERTY_IDS)} property ids, {NODE_PROCESSOR_NAME[2]!r}, "
        f"error 0x{NODE_PROCESSOR_ERROR[3]:04x}{NODE_PROCESSOR_ERROR[1]:04x}"
    )


def check_report_request(acib, rkof, ctab, sbd1):
    """What asks for a type 1 report: the class dispatch, its arm, and where the id is built.

    Read as instructions because the class arithmetic is an immediate and the id is a modified
    immediate, which is why a search for the plain MOVW form found nothing [2.6].
    """
    at, want = REQUEST_CLASS_BASE
    if wide_imm(acib, at) != want:
        return False, f"the class base at 0x{at:x} is {wide_imm(acib, at)}, not 0x{want:x}"
    at, want = REQUEST_CLASS_BOUND
    if u16(acib, at) != want:
        return False, f"the class bound at 0x{at:x} is 0x{u16(acib, at):04x}"
    if (u16(acib, REQUEST_BRANCH), u16(acib, REQUEST_BRANCH + 2)) != (0xE8DF, 0xF015):
        return False, f"0x{REQUEST_BRANCH:x} is no longer tbh [pc, r5, lsl #1]"
    arm, target = REQUEST_ARM
    at = REQUEST_TABLE + 2 * u16(acib, REQUEST_TABLE + 2 * REQUEST_INDEX)
    if at != arm:
        return False, f"class 0x{REQUEST_INDEX + 0x30:x} now branches to 0x{at:x}, not 0x{arm:x}"
    if u16(acib, arm) != MOV_R0_R8 - 0x20:  # mov r0, r4
        return False, f"the arm at 0x{arm:x} does not open with mov r0, r4"
    goes = wide_branch(acib, arm + 2)
    if goes != target:
        return False, f"the arm calls {goes and hex(goes)}, not 0x{target:x}"
    for at in REQUEST_ID_SITES:
        if mov_imm(acib, at) != REQUEST_ID:
            return False, f"0x{at:x} no longer builds 0x{REQUEST_ID:04x}"
    at, bytes_, literal_at, buffer_ = REQUEST_STAGING
    if u16(acib, at) != bytes_ or u32(acib, literal_at) != buffer_:
        return False, f"the staging copy at 0x{at:x} changed"
    return True, (
        f"class 0x{REQUEST_INDEX + 0x30:x} of {REQUEST_CLASS_BOUND[1] & 0xFF:#x} reaches "
        f"0x{target:x}, id 0x{REQUEST_ID:04x} built at {len(REQUEST_ID_SITES)} sites"
    )


def check_report_producer(acib, rkof, ctab, sbd1):
    """The two report producers in the controller: the message, the ids, the two copies. [2.6]

    The whole layout 2.6 publishes comes out of the first of them, so every number it writes is
    checked here, including the copy size that does not match the spacing of its destinations.
    The second is the generic sender, checked for the marker byte and the id it stamps.
    """
    if u32(acib, PRODUCER_POINTER) != PRODUCER | 1:
        return False, f"acib 0x{PRODUCER_POINTER:x} no longer points at 0x{PRODUCER:x}"
    at, want = PRODUCER_CONSTANT
    if movw(acib, at) != want:
        return False, f"the message constant at 0x{at:x} is not 0x{want:x}"
    at, want = PRODUCER_LENGTH
    if u16(acib, at) != 0x2000 | want:
        return False, f"the length at 0x{at:x} is 0x{u16(acib, at):04x}, not {want}"
    at, want = PRODUCER_ID_BASE
    if movw(acib, at) != want:
        return False, f"the id base at 0x{at:x} is not 0x{want:x}"
    for at, want in PRODUCER_COPIES:
        got = wide_imm(acib, at)
        if got != want:
            return False, f"the offset at 0x{at:x} is {got and hex(got)}, not 0x{want:x}"
    for at in PRODUCER_COPY_SIZES:
        if u16(acib, at) != 0x2200 | PRODUCER_COPY_SIZE:
            return False, f"the copy size at 0x{at:x} is 0x{u16(acib, at):04x}"
    at, want = SENDER_MARKER
    if u16(acib, at) != 0x2100 | want:
        return False, f"the sender's marker at 0x{at:x} is 0x{u16(acib, at):04x}"
    at, want = SENDER_ID
    if movw(acib, at) != want:
        return False, f"the sender at 0x{SENDER:x} no longer stamps 0x{want:x}"
    at, want = SENDER_LENGTH
    if u16(acib, at) != 0x2100 | want:
        return False, f"the sender's length at 0x{at:x} is 0x{u16(acib, at):04x}"
    at, want = SENDER_OBJECT
    if u32(acib, at) != want:
        return False, f"type 0's object literal at 0x{at:x} is 0x{u32(acib, at):08x}"
    for at, want in SENDER_COUNTS:
        if u16(acib, at) != want:
            return False, f"the word count at 0x{at:x} is 0x{u16(acib, at):04x}, not 0x{want:04x}"
    for at, want in SENDER_OFFSETS:
        got = wide_imm(acib, at)
        if got != want:
            return False, f"the offset at 0x{at:x} is {got and hex(got)}, not 0x{want:x}"
    at, first, second = SENDER_HALFWORD
    if (u16(acib, at), u16(acib, at + 2)) != (first, second):
        return False, f"0x{at:x} no longer reads the halfword at the object's +0x56"
    head, tail = SENDER_ID_ENCODING
    stamps = [
        at
        for at in range(0, len(acib) - 3, 2)
        if u16(acib, at) == head and u16(acib, at + 2) & 0xF0FF == tail
    ]
    if stamps != [SENDER_ID[0]]:
        return False, f"type 0's id is stamped at {[hex(at) for at in stamps]}"
    return True, (
        f"0x{PRODUCER:x}: id 0x{PRODUCER_ID_BASE[1] + 3:x}, {PRODUCER_LENGTH[1] - 4} payload "
        f"bytes, blocks at +0x45 and +0x85 copied 0x{PRODUCER_COPY_SIZE:x} at a time; "
        f"0x{SENDER:x} stamps 0x{SENDER_ID[1]:x} over 9 + 4 words and one more"
    )


def check_procedure_scheduler(acib, rkof, ctab, sbd1):
    """The per-link procedure scheduler: its id space, its table and two of its arms. [5.11]

    Decoded from the instructions rather than read off a table, because the two numbers that
    matter are immediates: the id the index counts from and the highest index allowed.
    """
    base = movw(acib, SCHEDULER_BASE_LOAD)
    if base != SCHEDULER_BASE_ID:
        return False, f"the base id is now {base and hex(base)}, not 0x{SCHEDULER_BASE_ID:x}"
    bound = u16(acib, SCHEDULER_BOUND_AT)
    if bound != 0x2800 | SCHEDULER_BOUND:
        return False, f"the bound at 0x{SCHEDULER_BOUND_AT:x} is now 0x{bound:04x}"
    if (u16(acib, SCHEDULER_BRANCH), u16(acib, SCHEDULER_BRANCH + 2)) != (0xE8DF, 0xF010):
        return False, f"0x{SCHEDULER_BRANCH:x} is no longer tbh [pc, r0, lsl #1]"
    arms = {}
    for index in range(SCHEDULER_BOUND + 1):
        at = SCHEDULER_TABLE + 2 * u16(acib, SCHEDULER_TABLE + 2 * index)
        arms.setdefault(at, []).append(index)
    if len(arms) != SCHEDULER_ARMS:
        return False, f"{len(arms)} distinct arms, not {SCHEDULER_ARMS}"
    for index, sender in SCHEDULER_SENDERS.items():
        at = SCHEDULER_TABLE + 2 * u16(acib, SCHEDULER_TABLE + 2 * index)
        if u16(acib, at) != MOV_R0_R8:
            return False, f"index 0x{index:x}'s arm at 0x{at:x} does not open with mov r0, r8"
        goes = wide_branch(acib, at + 2)
        if goes != sender:
            return False, f"index 0x{index:x} calls {goes and hex(goes)}, not 0x{sender:x}"
    return True, (
        f"{SCHEDULER_BOUND + 1} ids from 0x{base:x}, {len(arms)} arms, "
        f"0x1a -> 0x{SCHEDULER_SENDERS[0x1A]:x}"
    )


def check_standard_rate_roads(acib, rkof, ctab, sbd1):
    """Both standard roads to an extra ACL rate, and that each is closed. [5.12]

    These two arms are why this project can say HDR is announced nowhere a standard host can
    see it, so the bound and the page limit are checked as instructions, not described.
    """
    arm = ESCAPE_7F_TABLE + 2 * u16(acib, ESCAPE_7F_TABLE + 2 * (PACKET_TABLE_EXT - 1))
    if arm != PACKET_TABLE_ARM:
        return False, f"the packet-type-table arm is now 0x{arm:x}, not 0x{PACKET_TABLE_ARM:x}"
    if u16(acib, PACKET_TABLE_READ) != 0x7828:  # ldrb r0, [r5]
        return False, f"0x{PACKET_TABLE_READ:x} no longer reads the payload byte"
    if u16(acib, PACKET_TABLE_CMP) != 0x2801:  # cmp r0, #1
        return False, (
            f"the bound at 0x{PACKET_TABLE_CMP:x} is now "
            f"0x{u16(acib, PACKET_TABLE_CMP):04x}, not cmp r0, #1"
        )
    if u16(acib, PACKET_TABLE_ERROR_AT) != 0x2000 | PACKET_TABLE_ERROR:
        return False, f"0x{PACKET_TABLE_ERROR_AT:x} no longer answers 0x{PACKET_TABLE_ERROR:x}"

    arm = ESCAPE_7F_TABLE + 2 * u16(acib, ESCAPE_7F_TABLE + 2 * (FEATURES_EXT - 1))
    if arm != FEATURES_ARM:
        return False, f"the features arm is now 0x{arm:x}, not 0x{FEATURES_ARM:x}"
    goes = wide_branch(acib, FEATURES_CALL)
    if goes != FEATURES_HANDLER:
        return False, f"that arm calls {goes and hex(goes)}, not 0x{FEATURES_HANDLER:x}"
    if u16(acib, FEATURES_PAGE_CMP) != 0x2D03:  # cmp r5, #3
        return False, f"the page bound at 0x{FEATURES_PAGE_CMP:x} is no longer cmp r5, #3"
    if u16(acib, FEATURES_FILL_LEN) != 0x2108:  # movs r1, #8
        return False, f"0x{FEATURES_FILL_LEN:x} no longer fills eight bytes"
    if u16(acib, FEATURES_REPLY_OPCODE) != 0x217F:  # movs r1, #0x7f
        return False, f"0x{FEATURES_REPLY_OPCODE:x} no longer answers on escape 0x7F"
    if u16(acib, FEATURES_REPLY_EXT) != 0x2204:  # movs r2, #4
        return False, f"0x{FEATURES_REPLY_EXT:x} no longer answers with extended opcode 4"
    for at, want in ((FEATURES_PAGE_0_LOAD, FEATURES_PAGE_0),
                     (FEATURES_PAGE_12_LOAD, FEATURES_PAGE_12)):
        got = literal_word(acib, at)
        if got != want:
            return False, f"0x{at:x} loads {got and hex(got)}, not 0x{want:08x}"
    return True, (
        "packet-type table bounded to 0 or 1 with error 0x1e, feature pages bounded below 3 "
        f"and filled with eight zeros above, pages at 0x{FEATURES_PAGE_0:x} and "
        f"0x{FEATURES_PAGE_12:x}"
    )


def check_ctab_manifest(acib, rkof, ctab, sbd1):
    """That ctab is the firmware table again, compressed, and carries nothing else. [4.1]

    This claim is about the container rather than about an image, so it is read from the
    container: the table's own magic as a literal, none of libcompression's block magics
    anywhere, and each published entry's offset and size as three-byte little-endian literals
    in the table's own order.
    """
    at = ctab.find(CTAB_MAGIC)
    if at != CTAB_MAGIC_AT:
        return False, f"{CTAB_MAGIC.decode()!r} is at 0x{at:x}, not 0x{CTAB_MAGIC_AT:x}"
    for magic in CTAB_FRAMING:
        if magic in ctab:
            return False, f"{magic.decode()!r} appears, so this is a framed stream after all"
    head = ctab[:CTAB_MANIFEST_HEAD]
    place = -1
    for tag, offset, size in CTAB_MANIFEST:
        for gap in (b"", b"\x00"):
            found = head.find(offset.to_bytes(3, "little") + gap + size.to_bytes(3, "little"))
            if found >= 0:
                break
        if found < 0:
            return False, f"{tag}'s offset and size are not a literal pair in the manifest"
        if found <= place:
            return False, f"{tag} lands at 0x{found:x}, which is not after the entry before it"
        place = found
    return True, (
        f"{CTAB_MAGIC.decode()!r} at 0x{at:x}, {len(CTAB_MANIFEST)} entries in table order, "
        "no block magic"
    )


def check_a2dp_endpoints(acib, rkof, ctab, sbd1):
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
    ("extended PDU body builders", check_transmit_builders),
    ("mode-change event shape", check_event_shape),
    ("mode, result and reason names", check_names),
    ("name table bounds", check_name_table_bounds),
    ("property getter table", check_getter_table),
    ("audio DMA descriptor", check_dma_descriptor),
    ("ULLA session commands", check_session_commands),
    ("feature bit commands", check_feature_bit_commands),
    ("procedure state tables", check_procedure_tables),
    ("A2DP stream endpoints", check_a2dp_endpoints),
    ("report listeners", check_report_listeners),
    ("session command gates", check_session_command_gates),
    ("report producers", check_report_producer),
    ("report request path", check_report_request),
    ("DSP ULLA node", check_dsp_ulla_node),
    ("procedure scheduler", check_procedure_scheduler),
    ("ctab is the table again", check_ctab_manifest),
    ("no standard road to a new rate", check_standard_rate_roads),
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
    ap.add_argument("--payloads", help="directory holding acib.bin, rkof.bin and ctab.bin")
    ap.add_argument("--acib", help="the Bluetooth controller payload")
    ap.add_argument("--rkof", help="the RTKitOS features payload")
    ap.add_argument("--ctab", help="the compressed copy of the whole firmware table")
    ap.add_argument("--sbd1", help="the DSP payload, which holds the ULLA node of section 7")
    args = ap.parse_args()
    here = args.payloads

    def beside(name):
        return os.path.join(here, f"{name}.bin") if here else None

    acib = load(args.acib or beside("acib"), "acib")
    rkof = load(args.rkof or beside("rkof"), "rkof")
    ctab = load(args.ctab or beside("ctab"), "ctab")
    sbd1 = load(args.sbd1 or beside("sbd1"), "sbd1")

    print(
        f"acib {len(acib)} bytes, rkof {len(rkof)} bytes, "
        f"ctab {len(ctab)} bytes, sbd1 {len(sbd1)} bytes"
    )
    failed = 0
    for name, check in CHECKS:
        try:
            ok, detail = check(acib, rkof, ctab, sbd1)
        except Exception as exc:  # a claim that cannot even be read is a failed claim
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<32} {detail}")
        failed += 0 if ok else 1
    print(f"{len(CHECKS) - failed} of {len(CHECKS)} claims hold")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
