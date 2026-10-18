"""openulla: an independent implementation of Apple's ULLA setup protocol.

Every constant and every rule in this package cites the AirPods Pro 2 (USB-C) firmware,
build 9A348, or a measurement against real earbuds. Nothing is inferred. The firmware itself
is not redistributed and no disassembly of it appears here; `tools/fetch_firmware.sh` fetches
Apple's published update and `tools/ftab.py` extracts the payloads the citations name.

  aap          the host interface, AAP service 4 on L2CAP PSM 0x1001
  session      the earbud's session manager, its states, events and transition table
  sibling      the earbud-to-earbud messages
  lmp          the link-manager PDUs the two controllers exchange
  hci          the vendor commands and the vendor event that drive a controller
  bands        the radio's band plan and the rules that pick a band for a link
  codes        the numbered values, with the names the firmware's own tables give them
  controller   each end's side of the link-manager exchange
  sink         an earbud-shaped simulator, so a source can be exercised without hardware
  source       what a host sends, and what it cannot
  sim          a whole negotiation with no hardware, for trying it and for the tests
  transport    a real L2CAP channel, or a pair of queues
  describe     one place for the wording of every decode
  capture      finding this protocol's frames in a btsnoop capture

What this cannot do is carry audio: the mode change moves the link to a radio whose packet
format is produced by hardware, which docs/protocol.md section 7 sets out in full.
"""
from __future__ import annotations

__version__ = "0.1.0"

from . import (
    aap,
    bands,
    capture,
    codes,
    controller,
    describe,
    hci,
    lmp,
    session,
    sibling,
    sim,
    sink,
    source,
    transport,
)
from .errors import ProtocolError, Refused

__all__ = [
    "aap",
    "bands",
    "capture",
    "codes",
    "controller",
    "describe",
    "hci",
    "lmp",
    "session",
    "sibling",
    "sim",
    "sink",
    "source",
    "transport",
    "ProtocolError",
    "Refused",
    "__version__",
]
