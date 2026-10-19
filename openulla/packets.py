"""The controller's ACL packet-type table, including the six entries Bluetooth does not define.

This is the answer to "how are Apple's HDR packet modes selected on a link", and it is a table
rather than an argument. The controller keeps **four** parallel arrays of eighteen entries: a
32-bit allowed-mask per entry, the maximum payload each entry carries, the four-bit TYPE code
that goes in the baseband header, and **the slot count**, coded 0, 1 and 2 for one, three and
five slots [fw acib masks 0xf11a0, payloads 0xf11e8, TYPE codes 0xf120c, slot counts 0xf121e;
read by 0x137a6 to 0x137ca, 0x14584, 0x149ee and 0x4a3b4].

The fourth array matters for what follows: **the slot counts are stated by the firmware**, not
inferred. Every one of the eighteen below agrees with it.

**Twelve of the eighteen are the specification's, with exactly its payload maxima**, which is
what proves the table is what it looks like: 17, 27, 54, 83, 121, 183, 224, 339, 367, 552, 679
and 1021 bytes are the Bluetooth figures for DM1 through 3-DH5, and all twelve appear. Exactly
six entries are left over, and their allowed-mask bits are 16 to 21, which lie above the
sixteen-bit packet-type field a standard host can reach at all
[docs/measurements.md section 9].

Those six only become selectable when the link's active packet-type table is 2, a value only
Apple's own LMP request can negotiate [docs/protocol.md section 5.13]. Their TYPE codes are the
same ones tables 0 and 1 use, which is what a packet-type table is for: the same four header
bits mean a different packet in a different table.

**The payloads say the two rates differ by a factor of about two, and they do not say more than
that.** The three-slot pair stands at 1416/703 = 2.014 and the five-slot pair at 2586/1288 =
2.008. The one-slot pair stands at 242/112 = **2.16** and does not fit that pattern; why is not
established, and it is not left out of the count here as it once was. Dividing a payload by a
rate is scale-invariant, so the agreement within a slot length fixes the *ratio* of the two
rates and nothing about their size: 4 and 8 fit exactly as well as 4.5 and 9, or 3 and 6.
`payload_window_us` computes that window and `test_the_payload_ratios_say_a_factor_of_two...`
demonstrates the invariance rather than hiding it.

**The absolute rates come from Apple's filing**, which declares exactly two proprietary rates
for this device, HDR4 at 4 Mb/s and HDR8 at 8 Mb/s [docs/ulla.md 2.2]. What the firmware
supplies is the split into two groups of three, and it supplies that directly: a fifth array at
acib 0xf7804 holds the eighteen TYPE codes grouped by rate class, six basic-rate codes and then
four groups of three, so its indices 12 to 17 are these six in two rate groups [read at
acib 0xd2d92].

The firmware never writes "HDR" anywhere, so the naming below is this project's, marked as
such: `apple_4mbit` and `apple_8mbit` describe the grouping the firmware gives and the rates
Apple's filing declares, and the identification of the two is stated in the documentation
rather than asserted here.
"""
from __future__ import annotations

from dataclasses import dataclass

# The table a link uses. 0 and 1 are the specification's basic-rate and EDR tables; 2 is
# reachable only through Apple's own packet-type-table request [protocol.md 5.13].
TABLE_BASIC = 0
TABLE_EDR = 1
TABLE_APPLE = 2
MAX_TABLE = TABLE_APPLE  # the receive arm refuses anything above this [fw acib 0x3c0c2]


@dataclass(frozen=True)
class PacketType:
    """One entry of the controller's packet-type table, in firmware order."""

    index: int  # its position, which is also its priority: the chooser counts down from 17
    mask_bit: int  # its bit in the allowed mask [fw acib 0xf11a0]
    payload: int  # the most data bytes it carries [fw acib 0xf11e8]
    type_code: int  # the four-bit TYPE in the baseband header [fw acib 0xf120c]
    slots: int  # how many slots it occupies, which its TYPE code fixes
    name: str
    tables: tuple[int, ...]  # which packet-type tables can select it


# TYPE codes 3 and 4 are one-slot, 8 is one-slot in the higher tables, 0xA and 0xB are three
# slots, 0xE and 0xF are five. That mapping is the specification's, and the six extra entries
# reuse it, which is how their slot counts are known rather than guessed.
#
# The `tables` column has three sources and they are worth separating. For the twelve specified
# entries it is the specification's own: a table is a reinterpretation of the four header bits,
# so DH1 and 2-DH1 cannot both be selectable at once. For the six extra entries it is this
# firmware: their mask bits are 16 to 21, and the only branch of the packet-type mask filter
# that leaves those bits alone is the one taken when the active table is 2 [fw acib 0x1376a the
# test, 0x13772 to 0x13786 that branch]. DM1 stays in all three for the same reason read the
# other way: that branch clears only bits 16 to 21, so bit 3 survives it and the chooser can
# still pick DM1 whatever table is active.
TABLE: tuple[PacketType, ...] = (
    PacketType(0, 3, 17, 0x3, 1, "DM1", (0, 1, 2)),
    PacketType(1, 4, 27, 0x4, 1, "DH1", (0,)),
    PacketType(2, 1, 54, 0x4, 1, "2-DH1", (1,)),
    PacketType(3, 2, 83, 0x8, 1, "3-DH1", (1,)),
    PacketType(4, 16, 112, 0x4, 1, "apple_4mbit_1slot", (2,)),
    PacketType(5, 10, 121, 0xA, 3, "DM3", (0,)),
    PacketType(6, 11, 183, 0xB, 3, "DH3", (0,)),
    PacketType(7, 14, 224, 0xE, 5, "DM5", (0,)),
    PacketType(8, 17, 242, 0x8, 1, "apple_8mbit_1slot", (2,)),
    PacketType(9, 15, 339, 0xF, 5, "DH5", (0,)),
    PacketType(10, 8, 367, 0xA, 3, "2-DH3", (1,)),
    PacketType(11, 9, 552, 0xB, 3, "3-DH3", (1,)),
    PacketType(12, 12, 679, 0xE, 5, "2-DH5", (1,)),
    PacketType(13, 18, 703, 0xA, 3, "apple_4mbit_3slot", (2,)),
    PacketType(14, 13, 1021, 0xF, 5, "3-DH5", (1,)),
    PacketType(15, 20, 1288, 0xE, 5, "apple_4mbit_5slot", (2,)),
    PacketType(16, 19, 1416, 0xB, 3, "apple_8mbit_3slot", (2,)),
    PacketType(17, 21, 2586, 0xF, 5, "apple_8mbit_5slot", (2,)),
)

# The specification's own payload maxima, as the control that identifies the table.
SPECIFIED_PAYLOADS = (17, 27, 54, 83, 121, 183, 224, 339, 367, 552, 679, 1021)
# The bits of the allowed mask that exist only for table 2.
APPLE_MASK_BITS = (16, 17, 18, 19, 20, 21)
# What this project reads the two extra rates as, in Mb/s [see the module docstring].
APPLE_RATES = (4, 8)


# The payload header. The specification's one-slot form is eight bits, two of LLID, one of FLOW
# and a five-bit length; its multi-slot form is sixteen, with a ten-bit length at bit 3 and
# three bits above it left undefined. Apple's table uses a twelve-bit length at the same offset,
# spending two of those three, which is the least that 2586 needs. The two writers' masks are
# what fix the positions [fw acib 0x78ce2 masks 0xE007 for ten bits, 0x78cee masks 0x8007 for
# twelve; docs/protocol.md 7.3].
LENGTH_SHIFT = 3
LENGTH_BITS_ONE_SLOT = 5
LENGTH_BITS_SPECIFIED = 10
LENGTH_BITS_APPLE = 12
HEADER_MASK_SPECIFIED = 0xE007  # bits 0 to 2 and 13 to 15 are not the length
HEADER_MASK_APPLE = 0x8007  # bits 0 to 2 and bit 15 are not the length


def length_bits(entry: PacketType) -> int:
    """How many bits of payload header length this packet type's header carries.

    The one-byte header with its five-bit length belongs to the basic-rate one-slot packets
    alone: every EDR packet carries the two-byte header whatever its slot count, which is why
    2-DH1 and 3-DH1 are not in that case despite being one slot. Their 54 and 83 bytes would
    not fit five bits, and `test_every_payload_fits_its_own_length_field` is what says so.
    """
    if entry.slots == 1 and TABLE_BASIC in entry.tables:
        return LENGTH_BITS_ONE_SLOT
    if entry.tables == (TABLE_APPLE,):
        return LENGTH_BITS_APPLE
    return LENGTH_BITS_SPECIFIED


def payload_header(entry: PacketType, length: int, llid: int = 2, flow: int = 1) -> int:
    """Build the payload header for one packet of this type.

    LLID and FLOW occupy the low three bits in every form, and the length follows at bit 3 in a
    field whose width depends on the type. A length the field cannot express is an error here
    rather than a silently truncated header, which is the failure this layout exists to avoid.
    """
    if not 0 <= llid <= 3:
        raise ValueError("LLID is two bits")
    if flow not in (0, 1):
        raise ValueError("FLOW is one bit")
    bits = length_bits(entry)
    if not 0 <= length <= entry.payload:
        raise ValueError(f"{entry.name} carries at most {entry.payload} bytes")
    if length >= 1 << bits:
        raise ValueError(f"{entry.name}'s length field is {bits} bits, so {length} will not fit")
    return llid | (flow << 2) | (length << LENGTH_SHIFT)


def payload_length(entry: PacketType, header: int) -> int:
    """Read the length back out of a payload header of this packet type's form."""
    bits = length_bits(entry)
    return (header >> LENGTH_SHIFT) & ((1 << bits) - 1)


def by_mask_bit(bit: int) -> PacketType:
    """The entry a given allowed-mask bit selects."""
    for entry in TABLE:
        if entry.mask_bit == bit:
            return entry
    raise ValueError(f"no packet type uses mask bit {bit}")


def for_table(table: int) -> tuple[PacketType, ...]:
    """Every entry a link running this packet-type table can select."""
    return tuple(entry for entry in TABLE if table in entry.tables)


def choose(allowed: int) -> PacketType | None:
    """The entry the controller picks for an allowed mask, reproducing its own search.

    It walks the table from the last entry down and takes the first whose mask bit the allowed
    mask permits, so the largest payload wins [fw acib 0x137a8 the starting index, 0x137ae the
    mask load, 0x137b2 the test, 0x137b6 the decrement].
    """
    for entry in reversed(TABLE):
        if allowed & (1 << entry.mask_bit):
            return entry
    return None


def payload_window_us(entry: PacketType, rate_mbit: int) -> float:
    """How long this entry's payload takes at a given rate, in microseconds.

    The derivation behind the rate identification: at the right rate, the entries of one slot
    length all give the same answer. The specified entries do so at 1, 2 and 3 Mb/s, and the
    six extra ones do so at 4 and 8.
    """
    if rate_mbit <= 0:
        raise ValueError("a rate in Mb/s must be positive")
    return entry.payload * 8 / rate_mbit
