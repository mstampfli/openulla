"""The capture reader, against a capture this test writes itself.

Nothing here comes from a real capture: a real one holds Bluetooth addresses, and this
project keeps those off the internet. The records below are assembled from the layouts
openulla/capture.py documents, so the test also pins those layouts.
"""
import struct

import pytest

from openulla import aap, capture, hci, lmp
from openulla.errors import ProtocolError

MONITOR_COMMAND, MONITOR_EVENT, MONITOR_ACL_TX, MONITOR_ACL_RX = 2, 3, 4, 5
HANDLE = 0x000B
HOST_CID, BUD_CID = 0x0040, 0x0041


def btsnoop(records, datalink=capture.DATALINK_MONITOR):
    out = bytearray(capture.MAGIC + struct.pack(">II", 1, datalink))
    for flags, payload in records:
        out += struct.pack(capture.RECORD_HEADER, len(payload), len(payload), flags, 0, 0)
        out += payload
    return bytes(out)


def acl(cid, payload, handle=HANDLE, first=True):
    l2cap = struct.pack("<HH", len(payload), cid) + payload
    flags = 0x2 if first else 0x1
    return struct.pack("<HH", handle | (flags << 12), len(l2cap)) + l2cap


def signalling_connect(psm, scid):
    body = struct.pack("<HH", psm, scid)
    return struct.pack("<BBH", capture.L2CAP_CONNECT_REQUEST, 1, len(body)) + body


def signalling_response(dcid, scid, result=0):
    body = struct.pack("<HHHH", dcid, scid, result, 0)
    return struct.pack("<BBH", capture.L2CAP_CONNECT_RESPONSE, 1, len(body)) + body


def a_whole_session():
    """A capture of the part of a setup a host can really drive."""
    return btsnoop(
        [
            (MONITOR_ACL_TX, acl(capture.L2CAP_SIGNALLING_CID,
                                 signalling_connect(aap.PSM, HOST_CID))),
            (MONITOR_ACL_RX, acl(capture.L2CAP_SIGNALLING_CID,
                                 signalling_response(BUD_CID, HOST_CID))),
            (MONITOR_ACL_TX, acl(HOST_CID, aap.open_request())),
            (MONITOR_ACL_RX, acl(BUD_CID, aap.open_reply(1, 3))),
            (MONITOR_ACL_TX, acl(HOST_CID, aap.start_session(0))),
            (MONITOR_ACL_RX, acl(BUD_CID, aap.status_reply(0))),
            (MONITOR_COMMAND, hci.UllaSetup(HANDLE, 0, 0x0100, 0x03).command()),
            (MONITOR_EVENT, hci.ModeChangeEvent(HANDLE, 1, 0, 0xFF).to_bytes()),
        ]
    )


def test_the_reader_finds_every_frame_of_a_setup():
    found = list(capture.Reader().walk(capture.read_btsnoop(a_whole_session())))
    labels = [item.label for item in found]
    assert labels == [
        f"AAP on CID 0x{HOST_CID:04x}",
        f"AAP on CID 0x{BUD_CID:04x}",
        f"AAP on CID 0x{HOST_CID:04x}",
        f"AAP on CID 0x{BUD_CID:04x}",
        "HCI command",
        "HCI event",
    ]
    assert "open request" in found[0].lines[1]
    assert "accessory version 1.3" in found[1].lines[1]
    assert "START_SESSION" in found[2].lines[1]
    assert "OK" in found[3].lines[2]
    assert "the command that starts the transition" in found[4].lines[1]
    assert "ULLA_ON SUCCESS" in found[5].lines[2]


def test_the_channel_is_learned_from_the_signalling_and_not_assumed():
    reader = capture.Reader()
    list(reader.walk(capture.read_btsnoop(a_whole_session())))
    assert reader.psm_by_cid == {HOST_CID: aap.PSM, BUD_CID: aap.PSM}
    # Without the signalling, the same frames are not read as the accessory protocol.
    quiet = btsnoop([(MONITOR_ACL_TX, acl(HOST_CID, aap.open_request()))])
    assert not list(capture.Reader().walk(capture.read_btsnoop(quiet)))
    # ...unless the channel is named.
    named = capture.Reader(extra_cids={HOST_CID})
    assert len(list(named.walk(capture.read_btsnoop(quiet)))) == 1


def test_a_refused_l2cap_connection_binds_no_channel():
    blob = btsnoop(
        [
            (MONITOR_ACL_TX, acl(capture.L2CAP_SIGNALLING_CID,
                                 signalling_connect(aap.PSM, HOST_CID))),
            (MONITOR_ACL_RX, acl(capture.L2CAP_SIGNALLING_CID,
                                 signalling_response(BUD_CID, HOST_CID, result=2))),
            (MONITOR_ACL_RX, acl(BUD_CID, aap.open_reply(1, 3))),
        ]
    )
    reader = capture.Reader()
    assert not list(reader.walk(capture.read_btsnoop(blob)))
    assert BUD_CID not in reader.psm_by_cid


def test_a_frame_split_over_two_acl_packets_is_reassembled():
    frame = aap.open_request()
    l2cap = struct.pack("<HH", len(frame), HOST_CID) + frame
    head, tail = l2cap[:6], l2cap[6:]
    blob = btsnoop(
        [
            (MONITOR_ACL_TX, acl(capture.L2CAP_SIGNALLING_CID,
                                 signalling_connect(aap.PSM, HOST_CID))),
            (MONITOR_ACL_TX, struct.pack("<HH", HANDLE | (2 << 12), len(head)) + head),
            (MONITOR_ACL_TX, struct.pack("<HH", HANDLE | (1 << 12), len(tail)) + tail),
        ]
    )
    found = list(capture.Reader().walk(capture.read_btsnoop(blob)))
    assert len(found) == 1 and "open request" in found[0].lines[1]


def test_the_h4_datalink_is_read_too():
    blob = btsnoop(
        [(0, bytes((1,)) + hci.UllaSetup(HANDLE, 0, 0x0100, 0x03).command()),
         (1, bytes((4,)) + hci.ModeChangeEvent(HANDLE, 1, 0, 0xFF).to_bytes())],
        datalink=capture.DATALINK_H4,
    )
    packets = list(capture.read_btsnoop(blob))
    assert [p.kind for p in packets] == ["command", "event"]
    assert [p.outbound for p in packets] == [True, False]
    assert [item.label for item in capture.Reader().walk(packets)] == [
        "HCI command",
        "HCI event",
    ]


def test_a_file_that_is_not_a_capture_is_refused():
    with pytest.raises(ProtocolError):
        list(capture.read_btsnoop(b"not a capture at all"))
    with pytest.raises(ProtocolError):
        list(capture.read_btsnoop(capture.MAGIC + struct.pack(">II", 2, 1002)))
    with pytest.raises(ProtocolError):
        list(capture.read_btsnoop(capture.MAGIC + struct.pack(">II", 1, 999)))


def test_the_band_pdus_are_recognised_when_a_capture_carries_them():
    """A controller that exposed its link-manager traffic would show these."""
    from openulla.describe import describe_lmp

    assert "allowed-band mask" in " ".join(describe_lmp(lmp.AllowedBands(0xE6).to_bytes()))
