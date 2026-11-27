"""Check attribution on a synthetic BlueZ monitor file, including negative controls."""

import inspect_pi_lmp as diag


def test_reply_requires_the_right_connection_and_transaction(tmp_path):
    peer = bytes(range(6))
    other = bytes((9, 8)) + peer[-4:]
    info = b"\0" * 6 + (305).to_bytes(2, "little")

    def connection(remote, handle):
        return b"\x03\x0b\x00" + bytes((handle, 0)) + remote[::-1] + b"\x01\x00"

    def diagnostic(sent, pdu):
        body = bytearray(63)
        body[0] = 0 if sent else 1
        body[5:9] = peer[-4:]
        offset = 10 if sent else 13
        body[offset : offset + len(pdu)] = pdu
        return bytes(body)

    setup = diagnostic(True, bytes.fromhex("e6 26 0d 44 af 96 00 03 01"))
    accept = diagnostic(False, bytes.fromhex("06 73"))
    messages = [
        (10, info),
        (11, setup),  # the ACL has not opened
        (3, connection(peer, 1)),
        (11, setup),
        (3, b"\xff\x40\xb4" + setup),  # mirrored record
        (11, diagnostic(False, bytes.fromhex("07 73"))),  # other TID
        (11, accept),
        (3, connection(other, 2)),
        (11, setup),  # ambiguous four-byte suffix
    ]
    data = bytearray(diag.HEADER)
    for n, (op, body) in enumerate(messages):
        data.extend(diag.RECORD.pack(len(body), len(body), op, 0, n * 100))
        data.extend(body)
    path = tmp_path / "synthetic.btsnoop"
    path.write_bytes(data)
    result = diag.inspect(path, peer)
    assert [p["frame"] for p in result["setup_0x73"]] == [4]
    assert not result["setup_0x73"][0]["on_air_length_verified"]
    assert result["coverage_notes"] == {"unattributed_suffix": 2, "mirrored_diagnostic": 1}
    assert result["matched_peer_responses_to_local_0x73"] == [
        {
            "request_frame": 4,
            "response_frame": 7,
            "elapsed_us": 300,
            "tid": 0,
            "response": "accepted",
        }
    ]
