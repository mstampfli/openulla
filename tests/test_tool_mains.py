"""The command-line wrappers, run end to end on files this test makes.

The tools are what a reader actually runs, so their main() functions are exercised here:
the capture reader on a capture built in memory, the verifier on files that are not firmware,
which it must refuse cleanly rather than crash, and the container tool on a SuperBinary this
file builds record by record.
"""
import pathlib
import struct
import sys

import capture_decode
import ftab
import pytest
import verify_claims
from test_capture import a_whole_session


def test_the_capture_reader_finds_the_frames_and_says_how_many(tmp_path, capsys, monkeypatch):
    path = tmp_path / "session.btsnoop"
    path.write_bytes(a_whole_session())
    monkeypatch.setattr("sys.argv", ["capture_decode.py", str(path), "--raw"])
    assert capture_decode.main() == 0
    printed = capsys.readouterr().out
    assert "6 frames of this protocol" in printed
    assert "the command that starts the transition" in printed
    assert "the ULLA mode change" in printed
    assert "raw " in printed  # --raw was asked for


def test_a_capture_with_nothing_in_it_is_reported_as_such(tmp_path, capsys, monkeypatch):
    empty = tmp_path / "empty.btsnoop"
    empty.write_bytes(a_whole_session()[:16])  # the header alone
    monkeypatch.setattr("sys.argv", ["capture_decode.py", str(empty)])
    assert capture_decode.main() == 1
    assert "0 frames of this protocol" in capsys.readouterr().out


def test_a_file_that_is_not_a_capture_is_refused_with_a_message(tmp_path, monkeypatch):
    bad = tmp_path / "notes.txt"
    bad.write_text("this is not a capture")
    monkeypatch.setattr("sys.argv", ["capture_decode.py", str(bad)])
    with pytest.raises(SystemExit) as exit_info:
        capture_decode.main()
    assert "cannot read this capture" in str(exit_info.value)


def test_a_missing_file_is_refused_before_anything_is_read(monkeypatch):
    monkeypatch.setattr("sys.argv", ["capture_decode.py", "/nonexistent/air.btsnoop"])
    with pytest.raises(SystemExit) as exit_info:
        capture_decode.main()
    assert "no such file" in str(exit_info.value)


def test_the_verifier_refuses_files_that_are_not_the_firmware(tmp_path, capsys, monkeypatch):
    """Every claim fails and the tool says so, instead of raising."""
    acib, rkof = tmp_path / "acib.bin", tmp_path / "rkof.bin"
    acib.write_bytes(bytes(0x100000))
    rkof.write_bytes(bytes(0x200000))
    (tmp_path / "ctab.bin").write_bytes(bytes(0x1000))
    (tmp_path / "sbd1.bin").write_bytes(bytes(0x100000))
    monkeypatch.setattr("sys.argv", ["verify_claims.py", "--payloads", str(tmp_path)])
    assert verify_claims.main() == 1
    printed = capsys.readouterr().out
    assert printed.count("FAIL") == len(verify_claims.CHECKS)
    assert f"0 of {len(verify_claims.CHECKS)} claims hold" in printed


def test_the_verifier_says_how_to_get_the_payloads_when_they_are_missing(monkeypatch):
    monkeypatch.setattr("sys.argv", ["verify_claims.py"])
    with pytest.raises(SystemExit) as exit_info:
        verify_claims.main()
    assert "no acib payload" in str(exit_info.value)


def test_the_tools_are_all_executable_scripts():
    tools = pathlib.Path(__file__).resolve().parent.parent / "tools"
    for script in sorted(tools.glob("*.py")):
        text = script.read_text()
        assert text.startswith('"""') or text.startswith("#!"), script.name
        assert "def main(" in text or script.name in ("ftab.py", "tmap_keys.py"), script.name


def a_superbinary(payloads):
    """A SuperBinary whose header holds one record per payload, the layout ftab.py reads.

    The offset and length of the payload header are two big-endian words at 0x24; each record
    then carries its own length, its four-character tag, and its data's offset and length as
    two big-endian words at the record's own 0x20.
    """
    record_size = 0x40
    first = 0x80
    data_at = first + record_size * len(payloads)
    blob = bytearray(data_at)
    struct.pack_into(">2I", blob, 36, first, record_size * len(payloads))
    for index, (tag, data) in enumerate(payloads):
        at = first + record_size * index
        struct.pack_into(">I4s", blob, at, record_size, tag.encode("ascii"))
        struct.pack_into(">2I", blob, at + 32, len(blob), len(data))
        blob += data
    return bytes(blob)


def test_the_container_tool_lists_and_writes_out_a_whole_payload(tmp_path, capsys, monkeypatch):
    """The two commands that get ctab out of a SuperBinary, on one made here."""
    path = tmp_path / "firmware.uarp"
    path.write_bytes(a_superbinary([("FTAB", b"the table"), ("CTAB", b"the table again")]))
    monkeypatch.setattr("sys.argv", ["ftab.py", str(path), "payloads"])
    assert ftab.main(sys.argv) == 0
    listed = capsys.readouterr().out
    assert "FTAB" in listed and "CTAB" in listed and "size=       15" in listed

    out = tmp_path / "ctab.bin"
    monkeypatch.setattr("sys.argv", ["ftab.py", str(path), "payload", "CTAB", str(out)])
    assert ftab.main(sys.argv) == 0
    assert out.read_bytes() == b"the table again"
    assert "wrote 15 bytes of payload CTAB" in capsys.readouterr().out


def test_a_payload_that_is_not_in_the_container_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "firmware.uarp"
    path.write_bytes(a_superbinary([("FTAB", b"the table")]))
    monkeypatch.setattr("sys.argv", ["ftab.py", str(path), "payload", "NOPE", str(tmp_path / "x")])
    with pytest.raises(KeyError):
        ftab.main(sys.argv)
