"""The Bluetooth LMP feature pages, and which bits in them are defined.

This exists to answer one question with a measurement instead of an argument: **is Apple's HDR
declared anywhere a standard Bluetooth host could see it?** A new ACL rate has exactly one
specified place to be announced, the LMP feature pages, the way Enhanced Data Rate announced
itself in page 0 bits 25 and 26. So a set bit that the specification does not define would be
the HDR announcement, and an all-defined page set says HDR is not announced here at all.

The bit assignments are the Bluetooth Core specification's, Vol 2 Part C section 3.3: page 0 in
its feature-definition table, and the two extended pages in the tables after it. Only defined
bits are listed, so anything set and missing from these tables comes back from `undefined()`.

Measured against real earbuds in [docs/measurements.md](../docs/measurements.md) section 13, and
the firmware side of the same exchange is [docs/protocol.md](../docs/protocol.md) section 5.12.
"""
from __future__ import annotations

# Page 0. Bits 24, 34, 50, 55 and 59 to 62 are reserved by the specification and so are absent.
PAGE_0: dict[int, str] = {
    0: "3-slot packets",
    1: "5-slot packets",
    2: "encryption",
    3: "slot offset",
    4: "timing accuracy",
    5: "role switch",
    6: "hold mode",
    7: "sniff mode",
    8: "previously used",
    9: "power control requests",
    10: "channel quality driven data rate",
    11: "SCO link",
    12: "HV2 packets",
    13: "HV3 packets",
    14: "u-law log synchronous data",
    15: "A-law log synchronous data",
    16: "CVSD synchronous data",
    17: "paging parameter negotiation",
    18: "power control",
    19: "transparent synchronous data",
    20: "flow control lag, low bit",
    21: "flow control lag, middle bit",
    22: "flow control lag, high bit",
    23: "broadcast encryption",
    25: "EDR ACL 2 Mb/s",
    26: "EDR ACL 3 Mb/s",
    27: "enhanced inquiry scan",
    28: "interlaced inquiry scan",
    29: "interlaced page scan",
    30: "RSSI with inquiry results",
    31: "extended SCO link, EV3 packets",
    32: "EV4 packets",
    33: "EV5 packets",
    35: "AFH capable peripheral",
    36: "AFH classification peripheral",
    37: "BR/EDR not supported",
    38: "LE supported, controller",
    39: "3-slot EDR ACL packets",
    40: "5-slot EDR ACL packets",
    41: "sniff subrating",
    42: "pause encryption",
    43: "AFH capable central",
    44: "AFH classification central",
    45: "EDR eSCO 2 Mb/s",
    46: "EDR eSCO 3 Mb/s",
    47: "3-slot EDR eSCO packets",
    48: "extended inquiry response",
    49: "simultaneous LE and BR/EDR, controller",
    51: "secure simple pairing, controller",
    52: "encapsulated PDU",
    53: "erroneous data reporting",
    54: "non-flushable packet boundary flag",
    56: "link supervision timeout changed event",
    57: "variable inquiry TX power level",
    58: "enhanced power control",
    63: "extended features",
}

# Extended page 1. Everything from bit 4 up is reserved.
PAGE_1: dict[int, str] = {
    0: "secure simple pairing, host",
    1: "LE supported, host",
    2: "simultaneous LE and BR/EDR, host",
    3: "secure connections, host",
}

# Extended page 2. Everything from bit 13 up is reserved, and so is bit 7.
PAGE_2: dict[int, str] = {
    0: "connectionless peripheral broadcast, transmitter",
    1: "connectionless peripheral broadcast, receiver",
    2: "synchronization train",
    3: "synchronization scan",
    4: "inquiry response notification event",
    5: "generalized interlaced scan",
    6: "coarse clock adjustment",
    8: "secure connections, controller",
    9: "ping",
    10: "slot availability mask",
    11: "train nudging",
    12: "reserved for future use",
}

DEFINED: dict[int, dict[int, str]] = {0: PAGE_0, 1: PAGE_1, 2: PAGE_2}

# The highest page these earbuds answer with anything but zeros. Their controller bounds the
# requested page below three and fills eight zero bytes for anything above it
# [fw acib 0x2770a, 0x2770e], so there is no vendor page behind the standard ones.
MAX_PAGE = 2


def set_bits(page_bytes: bytes) -> list[int]:
    """Which bit numbers are set, in the specification's numbering (byte N holds bits 8N up)."""
    return [
        index * 8 + bit
        for index, byte in enumerate(page_bytes)
        for bit in range(8)
        if byte & (1 << bit)
    ]


def decode(page: int, page_bytes: bytes) -> list[tuple[int, str]]:
    """Every set bit with the name the specification gives it, unnamed ones called reserved."""
    names = DEFINED.get(page, {})
    return [(bit, names.get(bit, "RESERVED, not defined by the specification"))
            for bit in set_bits(page_bytes)]


def undefined(page: int, page_bytes: bytes) -> list[int]:
    """Set bits the specification does not define. A non-empty answer is the whole point.

    Anything here is a bit some device announces that no standard host can name, which is what
    a proprietary ACL rate would have to look like if it were announced at all.
    """
    names = DEFINED.get(page, {})
    return [bit for bit in set_bits(page_bytes) if bit not in names]
