"""The source side: what a host sends to start a ULLA session, and what it gets back.

Everything here is proven against real earbuds or read from the firmware; what a host cannot
do is also recorded, because it is the honest half of the picture:

  open the service and negotiate versions            [measured]
  ask for a session with itself and read the status  [measured]
  subscribe to the report classes                    [measured]
  see the manager refuse a second start until reset  [measured]

What it cannot do is finish the link: the next step is the controller-to-controller exchange
of docs/protocol.md section 5, which the source's own Bluetooth controller must emit. A
commodity controller neither exposes that interface nor implements the radio modes, and its
firmware is signed. `ControllerCommands` builds the vendor commands anyway, so that an
implementation on hardware that does expose them has them ready, and so that the bytes can be
compared against a capture.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import aap, bands, hci
from .codes import SessionStatus
from .errors import ProtocolError
from .transport import Transport


@dataclass
class Source:
    """A host driving one AAP service-4 session."""

    transport: Transport
    version: tuple[int, int] = (1, 2)
    accessory_version: tuple[int, int] | None = None

    def open_service(self, timeout: float | None = 2.0) -> aap.OpenReply:
        self.transport.send(aap.open_request(aap.SERVICE_ULLA, *self.version))
        frame = aap.parse_frame(self.transport.recv(timeout))
        if frame.ftype != aap.FrameType.OPEN_REPLY:
            raise ProtocolError(f"expected an open reply, got frame type {frame.ftype}")
        reply = aap.OpenReply.from_bytes(frame.body)
        if not reply.ok:
            raise ProtocolError(f"the accessory refused the open with status 0x{reply.status:02x}")
        self.accessory_version = (reply.major, reply.minor)
        return reply

    @property
    def version_pair(self) -> int:
        """The pair that decides the wrapper's class width. [fw rkof 0x6cf1a]"""
        major, minor = self.accessory_version or self.version
        return (major << 16) | minor

    def start_session(self, mode: int = 0) -> None:
        self.transport.send(aap.start_session(mode))

    def subscribe_reports(self, cls_: int = aap.CLASS_ULLA_REPORTS) -> None:
        self.transport.send(aap.subscribe_reports(cls_, aap.class_width(self.version_pair)))

    def stop_session(self, value: int = 0) -> None:
        self.transport.send(aap.stop_session(value))

    def start_stream(self) -> None:
        self.transport.send(aap.start_stream())

    def stop_stream(self) -> None:
        self.transport.send(aap.stop_stream())

    def read(self, timeout: float | None = 2.0) -> aap.Frame:
        return aap.parse_frame(self.transport.recv(timeout))

    def read_status(self, timeout: float | None = 2.0) -> int:
        """Read one status reply, opcode 0x37. [fw rkof 0x6e9cc]"""
        return aap.parse_status(self.read(timeout).body)

    def close(self) -> None:
        self.transport.close()


@dataclass
class ControllerCommands:
    """The vendor commands a source's own controller would need, as bytes.

    These are HCI command packets without the H4 transport byte; `hci.h4` adds it.
    """

    handle: int

    def start_transition(
        self, config_word: int = 0, flags: int = 0, extra_flags: int = 0, mode: int = 0
    ) -> bytes:
        """0xFE50, the command that makes the controller emit the setup request."""
        return hci.UllaSetup(
            handle=self.handle,
            extra_flags=extra_flags,
            config_word=config_word,
            flags=flags,
            mode=mode,
        ).command()

    def announce_bands(self, mask: int) -> tuple[bytes, bytes]:
        """0xFF79 and 0xFF7A, the pair that tells the controller which bands are allowed."""
        return hci.allowed_bands(mask)

    def enable_band_commands(self) -> bytes:
        """0xFF77, without which every other band command completes with 0x0C."""
        return hci.band_gate(True)

    def choose_band(self, index: int) -> bytes:
        """0xFF7C, which only takes a band the device's own mask allows."""
        return hci.set_link_band(self.handle, index)

    def read_band(self) -> bytes:
        return hci.read_link_band(self.handle)

    @staticmethod
    def mask_for(indexes: tuple[int, ...]) -> int:
        """A mask from band indexes, refusing what the controller would refuse."""
        mask = 0
        for i in indexes:
            bands.band(i)
            mask |= 1 << i
        bands.check_allowed_mask(mask)
        return mask


def describe_status(status: int) -> str:
    try:
        return SessionStatus(status).name
    except ValueError:
        return f"0x{status:04x}"
