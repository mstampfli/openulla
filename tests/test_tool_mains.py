"""The command-line wrappers, run end to end on files this test makes.

The tools are what a reader actually runs, so their main() functions are exercised here:
the capture reader on a capture built in memory, and the verifier on two files that are not
firmware, which it must refuse cleanly rather than crash.
"""
import pathlib

import capture_decode
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
