"""How frames get from here to there: a real L2CAP channel, or a pair of queues for tests.

The earbud identifies a session by the L2CAP connection it arrives on [fw rkof 0x6ca6c], so
one Transport is one session, and closing it resets the earbud's session state [measured].
"""
from __future__ import annotations

import socket
from collections import deque
from typing import Protocol

from .aap import PSM
from .errors import ProtocolError


class Transport(Protocol):
    """Anything that carries whole AAP frames in both directions."""

    def send(self, data: bytes) -> None: ...

    def recv(self, timeout: float | None = None) -> bytes: ...

    def close(self) -> None: ...


class L2capTransport:
    """A real sequential-packet L2CAP channel to a paired device, PSM 0x1001."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    @classmethod
    def connect(cls, address: str, psm: int = PSM) -> L2capTransport:
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise ProtocolError("this Python has no Bluetooth sockets; Linux with BlueZ is needed")
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        sock.connect((address, psm))
        return cls(sock)

    def send(self, data: bytes) -> None:
        self.sock.send(data)

    def recv(self, timeout: float | None = None) -> bytes:
        self.sock.settimeout(timeout)
        return self.sock.recv(1024)

    def close(self) -> None:
        self.sock.close()


class QueueTransport:
    """One end of an in-memory pair, so a source can be driven against a simulated sink."""

    def __init__(self) -> None:
        self.outbox: deque[bytes] = deque()
        self.inbox: deque[bytes] = deque()
        self.peer: QueueTransport | None = None
        self.closed = False

    @classmethod
    def pair(cls) -> tuple[QueueTransport, QueueTransport]:
        a, b = cls(), cls()
        a.peer, b.peer = b, a
        a.outbox = b.inbox
        b.outbox = a.inbox
        return a, b

    def send(self, data: bytes) -> None:
        if self.closed:
            raise ProtocolError("the channel is closed")
        self.outbox.append(bytes(data))

    def recv(self, timeout: float | None = None) -> bytes:
        if not self.inbox:
            raise TimeoutError("nothing queued")
        return self.inbox.popleft()

    def pending(self) -> int:
        return len(self.inbox)

    def close(self) -> None:
        self.closed = True
