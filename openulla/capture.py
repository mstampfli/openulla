"""Find this protocol's frames in a Bluetooth capture.

Two container layouts are read, both of them btsnoop files, which start with the magic
`btsnoop\\0` and a big-endian version and datalink type, then records of original length,
included length, flags, cumulative drops and a 64-bit timestamp:

  datalink 1002, HCI UART: each record is an H4 packet, its first byte the type, 1 command,
  2 ACL, 3 SCO, 4 event.
  datalink 2001, the BlueZ monitor format btmon writes: the record is the bare HCI packet and
  the low sixteen bits of the flags are the monitor opcode, of which 2 is a command, 3 an
  event, 4 ACL out and 5 ACL in. Read from the captures in this project: a command record
  begins with an opcode and a length, an event record with an event code and a length, and an
  ACL record with a handle, a length and then an L2CAP header.

What it then does with them: HCI commands and events go to openulla.describe, and ACL data is
reassembled per handle and followed as L2CAP, with the channel ids that carry PSM 0x1001
learned from the signalling channel so that the frames on them can be read as AAP.

Nothing is written out and nothing is published: a capture holds addresses, and under this
project's rules it stays on the machine that made it.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from . import aap
from .describe import describe_aap, describe_hci_command, describe_hci_event
from .errors import ProtocolError

MAGIC = b"btsnoop\x00"
HEADER_LENGTH = 16
RECORD_HEADER = ">IIIIq"
RECORD_HEADER_LENGTH = 24
DATALINK_H4 = 1002
DATALINK_MONITOR = 2001
MONITOR_KIND = {2: "command", 3: "event", 4: "acl", 5: "acl"}
H4_KIND = {1: "command", 2: "acl", 3: "sco", 4: "event"}
L2CAP_SIGNALLING_CID = 0x0001
L2CAP_CONNECT_REQUEST = 0x02
L2CAP_CONNECT_RESPONSE = 0x03


@dataclass(frozen=True)
class Packet:
    """One record of a capture, as an HCI packet without its transport byte."""

    index: int
    timestamp: int
    kind: str  # command, event, acl, sco or other
    outbound: bool
    data: bytes


@dataclass(frozen=True)
class Found:
    """Something this protocol recognises, and where it was."""

    packet: Packet
    label: str
    lines: list[str]
    data: bytes


def read_btsnoop(blob: bytes) -> Iterator[Packet]:
    """Walk a btsnoop file's records, whichever of the two datalinks it uses."""
    if blob[: len(MAGIC)] != MAGIC:
        raise ProtocolError("not a btsnoop file")
    version, datalink = struct.unpack_from(">II", blob, len(MAGIC))
    if version != 1:
        raise ProtocolError(f"btsnoop version {version} is not one this reads")
    if datalink not in (DATALINK_H4, DATALINK_MONITOR):
        raise ProtocolError(f"datalink {datalink} is not one this reads")
    offset, index = HEADER_LENGTH, 0
    while offset + RECORD_HEADER_LENGTH <= len(blob):
        _original, included, flags, _drops, timestamp = struct.unpack_from(
            RECORD_HEADER, blob, offset
        )
        payload = blob[offset + RECORD_HEADER_LENGTH : offset + RECORD_HEADER_LENGTH + included]
        offset += RECORD_HEADER_LENGTH + included
        index += 1
        if datalink == DATALINK_MONITOR:
            opcode = flags & 0xFFFF
            kind = MONITOR_KIND.get(opcode, "other")
            outbound = opcode in (2, 4)
            yield Packet(index, timestamp, kind, outbound, payload)
        else:
            if not payload:
                continue
            kind = H4_KIND.get(payload[0], "other")
            yield Packet(index, timestamp, kind, not flags & 1, payload[1:])


@dataclass
class Reader:
    """Follows a capture: tracks L2CAP channels, reassembles ACL and decodes what it knows."""

    psm_by_cid: dict[int, int] = field(default_factory=dict)
    pending_psm: dict[int, int] = field(default_factory=dict)
    fragments: dict[int, bytearray] = field(default_factory=dict)
    expected: dict[int, int] = field(default_factory=dict)
    aap_psm: int = aap.PSM
    extra_cids: set[int] = field(default_factory=set)

    def aap_cid(self, cid: int) -> bool:
        return cid in self.extra_cids or self.psm_by_cid.get(cid) == self.aap_psm

    def walk(self, packets: Iterable[Packet]) -> Iterator[Found]:
        for packet in packets:
            if packet.kind == "command":
                yield from self._try(packet, "HCI command", describe_hci_command, packet.data)
            elif packet.kind == "event":
                yield from self._try(packet, "HCI event", describe_hci_event, packet.data)
            elif packet.kind == "acl":
                yield from self._acl(packet)

    def _try(self, packet, label, decoder, data) -> Iterator[Found]:
        try:
            lines = decoder(data)
        except (ProtocolError, ValueError, IndexError):
            return
        if len(lines) > 1:  # a bare header is not worth reporting
            yield Found(packet, label, lines, data)

    def _acl(self, packet: Packet) -> Iterator[Found]:
        if len(packet.data) < 4:
            return
        handle_flags, length = struct.unpack_from("<HH", packet.data, 0)
        handle = handle_flags & 0x0FFF
        continuation = (handle_flags >> 12) & 0x3 == 1
        body = packet.data[4 : 4 + length]
        if continuation:
            buffer = self.fragments.get(handle)
            if buffer is None:
                return
            buffer += body
        else:
            if len(body) < 4:
                return
            l2cap_length = struct.unpack_from("<H", body, 0)[0]
            self.fragments[handle] = bytearray(body)
            self.expected[handle] = l2cap_length + 4
            buffer = self.fragments[handle]
        if len(buffer) < self.expected.get(handle, 0):
            return
        frame = bytes(buffer)
        del self.fragments[handle]
        yield from self._l2cap(packet, frame)

    def _l2cap(self, packet: Packet, frame: bytes) -> Iterator[Found]:
        length, cid = struct.unpack_from("<HH", frame, 0)
        payload = frame[4 : 4 + length]
        if cid == L2CAP_SIGNALLING_CID:
            self._signalling(payload)
            return
        if self.aap_cid(cid):
            yield from self._try(packet, f"AAP on CID 0x{cid:04x}", describe_aap, payload)

    def _signalling(self, payload: bytes) -> None:
        """Learn which channel ids carry the AAP service's PSM."""
        offset = 0
        while offset + 4 <= len(payload):
            code, _id, length = struct.unpack_from("<BBH", payload, offset)
            body = payload[offset + 4 : offset + 4 + length]
            offset += 4 + length
            if code == L2CAP_CONNECT_REQUEST and len(body) >= 4:
                psm, scid = struct.unpack_from("<HH", body, 0)
                self.pending_psm[scid] = psm
                self.psm_by_cid[scid] = psm
            elif code == L2CAP_CONNECT_RESPONSE and len(body) >= 6:
                dcid, scid, result = struct.unpack_from("<HHH", body, 0)
                if result == 0 and scid in self.pending_psm:
                    self.psm_by_cid[dcid] = self.pending_psm[scid]


def walk_file(path: str, extra_cids: Iterable[int] = ()) -> Iterator[Found]:
    """Read one capture from disk and yield what this protocol recognises in it."""
    with open(path, "rb") as handle:
        blob = handle.read()
    reader = Reader(extra_cids=set(extra_cids))
    yield from reader.walk(read_btsnoop(blob))
