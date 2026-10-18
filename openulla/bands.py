"""The radio's band plan, and the rules that pick a band for a link.

The table is a constant in the controller image: eight records of seven bytes at
acib 0xf52e8, indexed by the band index, the stride computed as index*8 - index
[fw acib 0x407ec]. Field order is fixed by the one function that uses the record as a
plan [fw acib 0x407dc]: it subtracts the byte at +6 from the channel number, multiplies by
the byte at +5, and adds the halfword at +0, returning the byte at +4 as the bandwidth.

  record            u16 start frequency, u8 width, u8 channels, u8 channel bandwidth,
                    u8 channel spacing, u8 first channel number  [all MHz but the counts]
  mask bit N        set exactly when country-code slot N is not 0xFF  [fw rkof 0x97fa4]
  mask refusals     0 and any mask with bit 3 set  [fw acib 0x19430]
  index from byte   31 - count_leading_zeros(byte)  [fw acib 0x193a0]
  no band           index 8  [fw acib 0x197f4, 0x1984c]
  band 0 fallback   dropped from a link's mask unless it is the whole mask
                    [fw acib 0x47014, 0x4702e]

Band 3's record is seven zero bytes, and three places refuse the index outright
[fw acib 0x18d10, the 0xFF79 gate at 0x19430, the walk at 0x2999c].
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .errors import ProtocolError

NO_BAND = 8
BAND_COUNT = 8
RECORD_SIZE = 7
TABLE_ADDRESS = 0xF52E8  # acib, for anyone re-deriving the table from their own image


@dataclass(frozen=True)
class Band:
    """One record of the controller's band table."""

    index: int
    start_mhz: int
    width_mhz: int
    channels: int
    channel_bandwidth_mhz: int
    spacing_mhz: int
    first_channel: int

    @property
    def defined(self) -> bool:
        return self.channels > 0

    @property
    def last_channel(self) -> int:
        return self.first_channel + self.channels - 1

    @property
    def end_mhz(self) -> int:
        return self.start_mhz + self.width_mhz

    def channel_numbers(self) -> range:
        return range(self.first_channel, self.first_channel + self.channels)

    def to_bytes(self) -> bytes:
        """The record as the firmware stores it, for a verifier to compare against."""
        return (
            self.start_mhz.to_bytes(2, "little")
            + bytes(
                (
                    self.width_mhz,
                    self.channels,
                    self.channel_bandwidth_mhz,
                    self.spacing_mhz,
                    self.first_channel,
                )
            )
        )


def _band(index: int, record: Sequence[int]) -> Band:
    return Band(
        index=index,
        start_mhz=record[0] | (record[1] << 8),
        width_mhz=record[2],
        channels=record[3],
        channel_bandwidth_mhz=record[4],
        spacing_mhz=record[5],
        first_channel=record[6],
    )


# The eight records, byte for byte as they stand at acib 0xf52e8.
BAND_TABLE: tuple[Band, ...] = tuple(
    _band(i, r)
    for i, r in enumerate(
        (
            (0x61, 0x09, 0x52, 0x0D, 0x16, 0x05, 0x00),
            (0x1E, 0x14, 0x64, 0x05, 0x14, 0x14, 0x0D),
            (0x67, 0x16, 0x78, 0x06, 0x14, 0x14, 0x12),
            (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
            (0x39, 0x17, 0x64, 0x05, 0x14, 0x14, 0x18),
            (0x9D, 0x17, 0x78, 0x06, 0x14, 0x14, 0x1D),
            (0x15, 0x18, 0x78, 0x06, 0x14, 0x14, 0x23),
            (0x8D, 0x18, 0x8C, 0x07, 0x14, 0x14, 0x29),
        )
    )
)

EMPTY_BAND = 3  # its record is all zeros and every path refuses the index


def band(index: int) -> Band:
    if not 0 <= index < BAND_COUNT:
        raise ProtocolError(f"band index {index} is outside 0..7")
    return BAND_TABLE[index]


def channel_frequency(index: int, channel: int, correction: int = 0) -> tuple[int, int]:
    """Centre frequency and bandwidth of a channel, in MHz. [fw acib 0x407dc]

    `correction` is the byte the firmware subtracts from the start frequency, taken from the
    RAM twin of the table at acib 0x10000640; it is 0 in every record of the constant table.
    """
    b = band(index)
    if not b.defined:
        raise ProtocolError(f"band {index} has no channels")
    if channel not in b.channel_numbers():
        raise ProtocolError(f"channel {channel} is not in band {index}")
    return (channel - b.first_channel) * b.spacing_mhz + (b.start_mhz - correction), b.channel_bandwidth_mhz


def band_of_channel(channel: int) -> int:
    """Which band owns a channel number in the single global numbering."""
    for b in BAND_TABLE:
        if b.defined and channel in b.channel_numbers():
            return b.index
    raise ProtocolError(f"channel {channel} is in no band")


def mask_from_country_codes(codes: Sequence[int]) -> int:
    """Build the allowed-band mask the way the earbud does. [fw rkof 0x97fa4]

    Eight slots, one per band; a slot of 0xFF means that band is unavailable.
    """
    if len(codes) != BAND_COUNT:
        raise ProtocolError(f"expected {BAND_COUNT} country-code slots, got {len(codes)}")
    mask = 0
    for i, code in enumerate(codes):
        if code != 0xFF:
            mask |= 1 << i
    return mask


def check_allowed_mask(mask: int) -> None:
    """Raise unless the controller would accept this mask. [fw acib 0x19430]"""
    if not 0 <= mask <= 0xFF:
        raise ProtocolError("the allowed-band mask is one byte")
    if mask == 0:
        raise ProtocolError("a mask of 0 is refused")
    if mask & (1 << EMPTY_BAND):
        raise ProtocolError(f"a mask with bit {EMPTY_BAND} set is refused")


def index_from_band_byte(value: int) -> int:
    """Turn a band byte back into an index the way 0xFF78 does. [fw acib 0x193a0]

    The firmware counts leading zeros and subtracts from 31, so a byte with several bits set
    yields the highest one.
    """
    if not 0 <= value <= 0xFF:
        raise ProtocolError("a band byte is one byte")
    if value == 0:
        return NO_BAND
    return value.bit_length() - 1


def link_mask(own: int, peer: int) -> int | None:
    """The mask a link ends up with, or None when the two do not intersect.

    The received mask is ANDed with our own and dropped if nothing is left
    [fw acib 0x45360]; then bit 0 is cleared unless it is the whole intersection
    [fw acib 0x47014, 0x4702e], so 2.4 GHz is used only when it is all both ends allow.
    """
    both = own & peer
    if both == 0:
        return None
    if both != 1:
        both &= ~1
    return both


def mask_bands(mask: int) -> tuple[int, ...]:
    """The band indexes a mask names, low bit first."""
    return tuple(i for i in range(BAND_COUNT) if mask & (1 << i))
