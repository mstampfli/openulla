"""The three A2DP stream endpoints the earbuds register, and what each advertises.

One copy of these bytes, because two things need them: `tools/verify_claims.py` checks them
against the firmware image, and `tools/bt_link_state.py` compares them with what BlueZ reports
the device advertising on the air. They agreed, byte for byte, when that was measured
[docs/measurements.md section 12].

  registration            one function registers all three [fw rkof 0x8a908..0x8a996,
                          registration 0x450ac]
  descriptors             [fw rkof 0x1c14d0, 0x1c14e8, 0x1c1500]; in each, the codec type is
                          the byte at +0xA, the capability length the byte at +0xB and the
                          pointer to the bytes the word at +0xC
  capability bytes        [fw rkof 0x1c1530, 0x1c153e, 0x1c1544], in the order vendor, AAC,
                          SBC
  conditions              AAC needs bit 1 of a configuration byte, the vendor endpoint needs
                          that byte to be nonzero at all [fw rkof 0x8a94e, 0x8a982]

What the vendor endpoint's eight codec-specific bytes mean is open; docs/ulla.md section 4.6
says what can and cannot be read out of them.
"""
from __future__ import annotations

APPLE_VENDOR_ID = 0x0000004C  # the first four bytes of the vendor capability, little-endian
APPLE_VENDOR_CODEC = 0x8001  # the two after them

# codec type -> (name, the capability bytes it advertises)
ENDPOINTS: dict[int, tuple[str, bytes]] = {
    0x00: ("SBC", bytes.fromhex("3fff0235")),
    0x02: ("AAC", bytes.fromhex("c0018c83e800")),
    0xFF: ("Apple vendor", bytes.fromhex("4c00000001800080008c0083e800")),
}


def name(codec: int) -> str:
    """What to call one of these codec types, or the number if it is none of them."""
    known = ENDPOINTS.get(codec)
    return known[0] if known is not None else f"codec type 0x{codec:02x}"


def vendor_specific(capability: bytes) -> bytes:
    """The eight bytes past the vendor and codec ids of the Apple capability. [ulla.md 4.6]"""
    if len(capability) != len(ENDPOINTS[0xFF][1]):
        raise ValueError("the Apple vendor capability is fourteen bytes")
    return capability[6:]
