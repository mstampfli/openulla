"""Exceptions, kept to two so a caller can tell "malformed" from "the peer said no"."""
from __future__ import annotations


class ProtocolError(ValueError):
    """A frame or PDU does not have the layout the firmware requires."""


class Refused(ProtocolError):
    """The peer refused, with the code the firmware would send."""

    def __init__(self, code: int, what: str = "") -> None:
        self.code = code
        super().__init__(f"refused with 0x{code:02x}{': ' + what if what else ''}")
