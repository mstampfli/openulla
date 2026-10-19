"""The command-line wrappers, run end to end on files this test makes.

The tools are what a reader actually runs, so their main() functions are exercised here:
the capture reader on a capture built in memory, the verifier on files that are not firmware,
which it must refuse cleanly rather than crash, and the container tool on a SuperBinary this
file builds record by record.
"""
import pathlib
import struct
import subprocess
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
    (tmp_path / "phyb.bin").write_bytes(bytes(0x10000))
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


def test_every_tool_answers_help_instead_of_a_traceback(tmp_path):
    """The first thing a reader types. A traceback here is a bug in the tool, not in them."""
    tools = pathlib.Path(__file__).resolve().parent.parent / "tools"
    for script in sorted(tools.glob("*.py")):
        run = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        printed = run.stdout + run.stderr
        assert "Traceback" not in printed, f"{script.name}: {printed[:300]}"
        assert printed.strip(), f"{script.name} said nothing at all"


def test_the_probe_builds_exactly_the_documented_commands(capsys, monkeypatch):
    """Its bytes come from the library, so a reader can check them without a controller."""
    import ulla_probe

    from openulla import hci

    rows = ulla_probe.probes(0x000B)
    names = [name for name, _, _, _ in rows]
    assert names[0] == "positive control", "the path has to be shown to work first"
    assert names[1] == "baseline", "the control has to run first or nothing can be read"
    assert ulla_probe.UNIMPLEMENTED == hci.vendor_opcode(0x3FF)
    # The five writers of 6.8 and 6.9 go out empty on purpose: a controller that has the opcode
    # but wants arguments refuses it with 0x12 before acting on anything.
    packet = {
        "0xFD02 packet size": hci.OPCODE_PACKET_SIZE,
        "0xFDEE packet table": hci.OPCODE_PACKET_TABLE_DIRECT,
        "0xFD7C size halfword": hci.OPCODE_PACKET_SIZE_HALFWORD,
        "0xFD7D size halfword": hci.OPCODE_PACKET_SIZE_HALFWORD_ALT,
        "0xFD5F internal size": hci.OPCODE_PACKET_SIZE_INTERNAL,
    }
    by_name = {name: (opcode, data) for name, opcode, data, _ in rows}
    for name, opcode in packet.items():
        assert name in by_name, f"{name} is documented but not probed"
        assert by_name[name][0] == opcode
        assert by_name[name][1] == hci.command(opcode), f"{name} must carry no parameters"
        assert len(by_name[name][1]) == 3
    for name, opcode, data, expected in rows:
        assert data[:2] == opcode.to_bytes(2, "little"), name
        assert data[2] == len(data) - 3, f"{name} declares the wrong parameter length"
        assert expected, name
    # The two 0xFE55 probes straddle the bound protocol.md 6.6 publishes.
    in_range = dict((n, d) for n, _, d, _ in rows)["0xFE55 in range"]
    out_of_range = dict((n, d) for n, _, d, _ in rows)["0xFE55 out of range"]
    assert int.from_bytes(in_range[3:], "little", signed=True) == 800
    assert int.from_bytes(out_of_range[3:], "little", signed=True) == 801

    monkeypatch.setattr("sys.argv", ["ulla_probe.py", "--dry-run"])
    assert ulla_probe.main() == 0
    printed = capsys.readouterr().out
    assert "0xfe55" in printed and "55 fe 04 20 03 00 00" in printed


def test_the_probe_refuses_to_report_when_its_positive_control_is_refused(capsys, monkeypatch):
    """A blanket refusal on every row is also what a path reaching no controller looks like.

    The negative baseline cannot tell those apart, which made the tool a check that could not
    fail: a socket that swallowed every command would have printed "this controller implements
    none of them" just the same. So the positive control decides whether the run is readable at
    all, and this covers both verdicts plus the one that must not be reported.
    """
    import ulla_probe

    monkeypatch.setattr(ulla_probe, "open_socket", lambda adapter: object())

    def answer(control):
        def ask(_sock, data):
            opcode = int.from_bytes(data[:2], "little")
            if opcode == ulla_probe.INTEL_READ_VERSION:
                return control
            return (ulla_probe.EVT_CMD_STATUS, 0x0C, opcode, b"")
        return ask

    good = (ulla_probe.EVT_CMD_COMPLETE, 0x00, ulla_probe.INTEL_READ_VERSION, b"\x37\x19")
    monkeypatch.setattr(ulla_probe, "ask", answer(good))
    monkeypatch.setattr("sys.argv", ["ulla_probe.py"])
    assert ulla_probe.main() == 0
    printed = capsys.readouterr().out
    assert "the refusals below are its own" in printed
    assert "implements none of them" in printed
    assert "interesting case" not in printed, "the control must not be reported as a finding"

    refused = (ulla_probe.EVT_CMD_STATUS, 0x0C, ulla_probe.INTEL_READ_VERSION, b"")
    monkeypatch.setattr(ulla_probe, "ask", answer(refused))
    assert ulla_probe.main() == 1, "a run whose control failed must not report a result"
    printed = capsys.readouterr().out
    assert "proves nothing" in printed
    # Keyed on the verdict line, not on "implements none of them": the refusal message explains
    # itself with that same phrase, so matching it would pass for the wrong reason.
    assert "Every ULLA opcode answered exactly like that" not in printed


def test_the_probe_names_every_status_it_can_print():
    """read() must not raise on a status the standard does not name."""
    import ulla_probe

    assert "success" in ulla_probe.read(0x00)
    assert "COMMAND_DISALLOWED" in ulla_probe.read(0x0C)
    assert "unnamed" in ulla_probe.read(0xEE)


def test_the_predictions_are_control_first_and_cover_the_published_modes():
    import aap_probe

    from openulla.codes import Mode

    modes = [mode for mode, _ in aap_probe.predictions()]
    assert modes[0] == 0, "the control has to run first or silence cannot be read"
    assert int(Mode.ULLA_LITE_ON) in modes, "the lite mode is one of the three the firmware names"
    assert any(m not in set(int(x) for x in Mode) for m in modes), "no out-of-range byte tried"


def test_the_mode_predictions_read_the_status_reply_not_the_frame_count(capsys, monkeypatch):
    """Every branch of the verdict, and the reason it keys on the status reply.

    A real pair volunteers its own status frames on the same channel, so counting frames
    cannot tell which answer arrived; this is the defect the first hardware run exposed.
    """
    import aap_probe

    from openulla import aap

    class Args:
        simulate = True
        device = None

    noise = aap.data_frame(0x0009, bytes.fromhex("2f00000000"))

    def runs(statuses):
        order = iter(statuses)

        def fake_drain(transport, seconds, label):
            if label == "open":
                return []
            status = next(order)
            # The volunteered frame comes first on purpose: the reply has to be found by its
            # opcode, not by being the only thing in the list.
            return [noise] if status is None else [noise, aap.status_reply(status)]

        return fake_drain

    monkeypatch.setattr(aap_probe, "drain", runs([0, 0, 0]))
    assert aap_probe.run_predictions(Args(), 0.01, 1, 2) == 0
    assert capsys.readouterr().out.count("so the byte is accepted.") == 2

    monkeypatch.setattr(aap_probe, "drain", runs([0, None, 0]))
    aap_probe.run_predictions(Args(), 0.01, 1, 2)
    assert "IS validated somewhere" in capsys.readouterr().out

    monkeypatch.setattr(aap_probe, "drain", runs([0, 0, 0x1776]))
    aap_probe.run_predictions(Args(), 0.01, 1, 2)
    assert "answered status 0x1776 where the control answered 0x0000" in capsys.readouterr().out

    monkeypatch.setattr(aap_probe, "drain", runs([None, None, None]))
    aap_probe.run_predictions(Args(), 0.01, 1, 2)
    assert "says nothing about the other two" in capsys.readouterr().out


def test_the_status_reader_finds_the_reply_among_volunteered_frames():
    import aap_probe

    from openulla import aap

    noise = aap.data_frame(0x0009, bytes.fromhex("3300000000"))
    assert aap_probe.status_of([noise]) is None
    assert aap_probe.status_of([noise, aap.status_reply(0), noise]) == 0
    assert aap_probe.status_of([aap.status_reply(0x1776)]) == 0x1776
    assert aap_probe.status_of([b"\x00\x01"]) is None  # a short frame is not a reply


def test_the_payload_survey_reports_the_control_beside_every_count(tmp_path, capsys, monkeypatch):
    """The control is the tool's whole reason to exist, so it is what gets tested.

    A two-byte pattern occurs by chance about once per 64 KB, so counting a vendor opcode in a
    megabyte payload finds it a dozen times whether or not that payload has ever heard of it.
    The first pass at this question was fooled exactly that way. These two synthetic payloads
    are the two outcomes: one that genuinely carries the material, and one of random bytes that
    carries nothing but will still score hits.
    """
    import random
    import struct

    import payload_survey

    from openulla import hci

    rng = random.Random(20261019)
    noise = bytearray(rng.randrange(256) for _ in range(300_000))
    (tmp_path / "noise.bin").write_bytes(bytes(noise))

    # A payload that really does hold the material: the name, and the table signature.
    real = bytearray(rng.randrange(256) for _ in range(60_000))
    real[1000:1016] = b"\x00ULLA 1.1 here\x00\x00"
    for offset, value in ((2000, 2586), (2050, 1416), (2100, 1288)):
        struct.pack_into("<H", real, offset, value)
    (tmp_path / "real.bin").write_bytes(bytes(real))

    monkeypatch.setattr("sys.argv", ["payload_survey.py", "--payloads", str(tmp_path)])
    assert payload_survey.main() == 0
    printed = capsys.readouterr().out

    # The name and the table signature are found where they are, and not where they are not.
    assert "The link is named in 1 of 2 payloads: real." in printed
    assert "signature appears in 1: real." in printed
    # Every payload gets a verdict, and random bytes must not read as carrying opcodes.
    assert printed.count("indistinguishable from chance") == 2, printed
    assert "ABOVE the control" not in printed

    # And the control really is the discriminator: the noise payload does score opcode hits,
    # so a tool that printed counts alone would have reported them as findings.
    hits = sum(bytes(noise).count(struct.pack("<H", op)) for op in payload_survey.DOCUMENTED)
    assert hits > 0, "the noise payload scored no hits, so this proves nothing"
    assert hci.OPCODE_PACKET_SIZE in payload_survey.DOCUMENTED
    assert not set(payload_survey.DOCUMENTED) & set(payload_survey.CONTROL)


def test_the_survey_needs_a_nul_on_both_sides_to_call_something_a_string():
    """Two `bx lr` instructions read as "pGpG"; a run inside code is not a string table."""
    import payload_survey

    code = b"\x70\x47" * 40  # forty bx lr, which is printable and is not text
    assert payload_survey.readable_strings(code) == []
    assert payload_survey.readable_strings(b"\x00AppleAOPVoice\x00") == ["AppleAOPVoice"]
    # Too short to count, so a stray fragment cannot inflate the figure an absence rests on.
    assert payload_survey.readable_strings(b"\x00short\x00") == []
