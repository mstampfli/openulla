"""The sink server's own loop, driven through a channel that is not a socket.

tools/ulla_sink.py needs real Bluetooth to accept a connection, so the loop that reads frames
and writes answers is tested here against a stand-in channel. What it must do is answer with
the sink's frames, log what it decodes, and reset the session when the channel closes.
"""
import pytest
import ulla_sink  # the tools directory is on the path through conftest.py

from openulla import aap
from openulla.codes import SessionStatus
from openulla.session import State

ADDRESS = "11:22:33:44:55:66"


class Channel:
    """A channel that hands over a fixed list of frames and keeps what was written."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    def recv(self, _size):
        return self.frames.pop(0) if self.frames else b""

    def send(self, data):
        self.sent.append(bytes(data))
        return len(data)


def test_the_server_answers_a_whole_session_on_one_channel(capsys):
    channel = Channel([aap.open_request(), aap.start_session(0), aap.start_stream()])
    ulla_sink.serve_one(channel, ADDRESS, advance=True)
    opened = aap.OpenReply.from_bytes(aap.parse_frame(channel.sent[0]).body)
    assert opened.ok and (opened.major, opened.minor) == (1, 3)
    assert aap.parse_status(aap.parse_frame(channel.sent[1]).body) == SessionStatus.OK
    printed = capsys.readouterr().out
    assert "connected: " + ADDRESS in printed
    assert "session was in STREAM_OPEN" in printed  # the closing line says where it got to
    assert "simulated event" in printed  # and every supplied event is named


def test_a_passive_server_sends_only_what_the_frames_earn(capsys):
    channel = Channel([aap.open_request(), aap.start_session(0)])
    ulla_sink.serve_one(channel, ADDRESS, advance=False)
    assert len(channel.sent) == 1  # the open reply, and no status reply
    assert "session was in HOST_SETUP" in capsys.readouterr().out


def test_a_frame_the_sink_refuses_is_reported_and_not_answered(capsys):
    channel = Channel([aap.open_request()[:12]])
    ulla_sink.serve_one(channel, ADDRESS, advance=True)
    assert channel.sent == []
    assert "refused with 0x72" in capsys.readouterr().out


def test_the_closing_line_shows_the_session_reset(capsys):
    channel = Channel([aap.open_request(), aap.start_session(0)])
    ulla_sink.serve_one(channel, ADDRESS, advance=True)
    printed = capsys.readouterr().out
    assert f"closed: {ADDRESS}, session was in" in printed
    assert f"now {State.IDLE.name}" in printed


def test_the_address_is_turned_into_the_order_the_stack_uses(capsys):
    """The peer the sink records is the address bytes least significant first."""
    channel = Channel([aap.open_request(), aap.start_session(1)])
    ulla_sink.serve_one(channel, "aa:bb:cc:dd:ee:ff", advance=False)
    printed = capsys.readouterr().out
    assert "connected: aa:bb:cc:dd:ee:ff" in printed


def test_the_sink_forwards_a_statistics_command_to_its_sibling():
    """A declared behaviour nothing exercised: coverage showed this branch never ran.

    The sink answers CONFIGURE_STATISTICS by forwarding it to the sibling rather than replying
    to the host, which is the shape protocol.md gives it, and the action it records names the
    firmware address the forwarding was read from. Without this test the branch was documented
    and unproven, which reads as done and is not.
    """
    from openulla import aap, sibling
    from openulla.sink import Sink

    peer = bytes.fromhex("aabbccddeeff")
    sink = Sink()
    sink.receive(aap.open_request(), peer)
    body = bytes([0x07, 0x01])
    assert sink.receive(aap.data_frame(aap.Opcode.CONFIGURE_STATISTICS, body), peer) == []
    forwarded = [a for a in sink.actions if a.kind == "sibling"]
    assert len(forwarded) == 1, sink.actions
    assert forwarded[0].data == sibling.frame(sibling.Message.CONFIGURE_STATISTICS, body)
    # Parsed back rather than only compared, so the body really survives the forwarding.
    parsed = sibling.parse_frame(forwarded[0].data)
    assert parsed.message == sibling.Message.CONFIGURE_STATISTICS
    assert parsed.body == body
    assert "rkof" in forwarded[0].fw


def test_the_sink_refuses_what_it_has_no_answer_for_instead_of_inventing_one():
    """Three refusal paths that were all unexercised. Each one is a deliberate non-answer."""
    from openulla import aap
    from openulla.errors import ProtocolError
    from openulla.sink import Sink

    peer = bytes.fromhex("aabbccddeeff")

    # A command before the service is opened: not established in the firmware, so refused.
    with pytest.raises(ProtocolError, match="open service 4 first"):
        Sink().receive(aap.data_frame(aap.Opcode.START_SESSION, b"\x01"), peer)

    # An opcode this sink does not implement.
    sink = Sink()
    sink.receive(aap.open_request(), peer)
    with pytest.raises(ProtocolError, match="not one this sink answers"):
        sink.receive(aap.data_frame(0x7E, b""), peer)

    # A wrapped report of a class it does not implement. The wrapper is built by the library so
    # that the frame is well formed and only the class is unknown; an unparseable one would
    # refuse for the wrong reason, which is the first version of this assertion's own mistake.
    wrapped = Sink()
    wrapped.receive(aap.open_request(), peer)
    width = aap.class_width(wrapped.sessions[peer].version_pair)
    unknown = aap.CLASS_ULLA_REPORTS + 1
    with pytest.raises(ProtocolError, match="report class"):
        wrapped.receive(aap.wrapper(unknown, b"\x00", width), peer)
