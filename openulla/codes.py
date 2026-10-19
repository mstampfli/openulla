"""Numbered values the ULLA setup uses, with the names the firmware's own tables give them.

Nothing here is invented. Each group cites where its numbering was read:

  mode 0..2           rkof 0x1be190, three name pointers indexed by the mode value
  result 0..4         rkof 0x1be5bc, five name pointers indexed by the result value
                      Neither index is bounds-checked by the formatter that uses them
                      [rkof 0x2774c, the loads at 0x27760 and 0x27766], so a value past the
                      end of either list prints a neighbouring table's name rather than an
                      error. The reason byte below is the one that is checked.
  reject reason       rkof 0x2777c: the formatter at 0x2774c branches through a byte table
                      at 0x2777c for reasons 0..6 and a second at 0x2778c for 0x81..0x87,
                      and tests 0xaa and 0xff directly (0x27798, 0x2779c). Reason 2 has no
                      name of its own: its slot points at the same "invalid" text as any
                      out-of-range value, so the secondary list is 0, 1, 3, 4, 5, 6.
  session status      rkof 0x6e9cc, and its only two callers 0x271f8 (0) and 0x2751c (0x1776)
  AAP open rejections rkof 0x40600, 0x406b4, 0x407ae, 0x406ea
  HCI error codes     acib 0x1da40 and 0x19430 return them, and acib 0x6783c sends 0x24 as
                      a link-manager reason; the names are BlueZ's, from
                      /usr/include/bluetooth/hci.h lines 156-180

Names are shortened from the firmware's own (REASON_SECONDARY_ROLE_NOT_INITIATED becomes
SECONDARY_ROLE_NOT_INITIATED); the numbers are untouched.
"""
from __future__ import annotations

from enum import IntEnum


class Mode(IntEnum):
    """What the radio is doing, as reported by the mode-change event. [fw rkof 0x1be190]"""

    BT_ACL_ACTIVE = 0
    ULLA_ON = 1
    ULLA_LITE_ON = 2


class Result(IntEnum):
    """How a mode change ended. [fw rkof 0x1be5bc]"""

    SUCCESS = 0
    ERROR = 1
    FAIL_TO_BE_ESTABLISHED = 2
    SETUP_REJECTED_BY_PEER = 3
    LSTO_ERROR = 4


class RejectReason(IntEnum):
    """Reason byte of the link-manager not-accepted PDU. [fw rkof 0x2777c, names 0x1dea6a]

    2 is absent on purpose: the firmware's own table sends it to the "invalid" text.
    """

    NO_SPECIFIC = 0
    SECONDARY_ROLE_NOT_INITIATED = 1
    SECONDARY_DISALLOWED_ACTIVITY = 3
    SECONDARY_UTP_NOT_IN_ACTIVE_MODE = 4
    SECONDARY_ILLEGAL_NUM_CONNECTIONS = 5
    SECONDARY_INVALID_SOURCE_CONNECTION = 6
    PRIMARY_ROLE_NOT_INITIATED = 0x81
    PRIMARY_ULLA_ALREADY_CONNECTED = 0x82
    PRIMARY_DISALLOWED_ACTIVITY = 0x83
    PRIMARY_UTP_NOT_IN_ACTIVE_MODE = 0x84
    PRIMARY_ILLEGAL_NUM_CONNECTIONS = 0x85
    PRIMARY_INVALID_SOURCE_CONNECTION = 0x86
    PRIMARY_SNOOP_DISCONNECTED = 0x87
    ULLA_LITE_UTP_DISCONNECTED = 0xAA
    NOT_APPLICABLE = 0xFF


def reason_name(value: int) -> str:
    """Name a reason byte the way the firmware's formatter does, "invalid" included."""
    try:
        return RejectReason(value).name
    except ValueError:
        return "INVALID_REASON"


# The identifiers the firmware's own name tables hold for these values, kept exactly so that
# tools/verify_claims.py can compare them against the image and a reader can grep a log.
FIRMWARE_NAMES: dict[int, str] = {
    Mode.BT_ACL_ACTIVE: "ULLA_MODE_BT_ACL_ACTIVE",
    Mode.ULLA_ON: "ULLA_MODE_ON",
    Mode.ULLA_LITE_ON: "ULLA_LITE_MODE_ON",
}
RESULT_FIRMWARE_NAMES: dict[int, str] = {
    Result.SUCCESS: "ULLA_MODE_CHANGE_SUCCESS",
    Result.ERROR: "ULLA_MODE_CHANGE_ERROR",
    Result.FAIL_TO_BE_ESTABLISHED: "ULLA_MODE_CHANGE_FAIL_TO_BE_ESTABLISHED",
    Result.SETUP_REJECTED_BY_PEER: "ULLA_MODE_CHANGE_SETUP_REJECTED_BY_PEER_ERROR",
    Result.LSTO_ERROR: "ULLA_MODE_CHANGE_LSTO_ERROR",
}
REASON_FIRMWARE_NAMES: dict[int, str] = {
    reason: f"REASON_{reason.name}" for reason in RejectReason
}


class SessionStatus(IntEnum):
    """Status word of the AAP status reply, opcode 0x37. [fw rkof 0x6e9cc]"""

    OK = 0
    SIBLING_TRANSPORT_NOT_READY = 0x1776


class AapReject(IntEnum):
    """Why the accessory refuses to open a service. [fw rkof 0x40600, 0x406b4, 0x407ae, 0x406ea]"""

    ALREADY_IN_STATE = 0x67
    NO_FREE_SESSION_SLOT = 0x68
    FRAME_TOO_SHORT = 0x72
    SESSION_EXISTS = 0x85


class HciError(IntEnum):
    """The standard error codes this firmware uses. Names from BlueZ hci.h 156-180.

    The last one is not an HCI reply but a link-manager reason: the sink sends it in a
    not-accepted PDU when it cannot hand a setup request to its sibling, and the firmware's
    own reason formatter has no name for it [fw acib 0x6783c, protocol.md 5.10].
    """

    COMMAND_DISALLOWED = 0x0C
    UNSUPPORTED_FEATURE = 0x11
    INVALID_PARAMETERS = 0x12
    REPEATED_ATTEMPTS = 0x17
    LMP_PDU_NOT_ALLOWED = 0x24
