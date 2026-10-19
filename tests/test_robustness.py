"""Every parser in this package answers random bytes with a ProtocolError, never a crash.

A decoder gets pointed at captures and logs, so the contract that matters is that malformed
input is refused in one recognisable way. ProtocolError is a ValueError, so a caller can
catch either; what must not happen is an IndexError, a struct error or a bare ValueError from
an enum deep inside.
"""
import random

from openulla import aap, avdtp, capture, describe, hci, lmp, sibling
from openulla.errors import ProtocolError

PARSERS = {
    "aap.parse_frame": aap.parse_frame,
    "aap.parse_payload": aap.parse_payload,
    "aap.parse_wrapper": aap.parse_wrapper,
    "aap.parse_status": aap.parse_status,
    "aap.parse_report": aap.parse_report,
    "aap.OpenReply.from_bytes": aap.OpenReply.from_bytes,
    "lmp.parse": lmp.parse,
    "lmp.parse_header": lmp.parse_header,
    "lmp.SetupRequest.from_bytes": lmp.SetupRequest.from_bytes,
    "lmp.Configuration.from_bytes": lmp.Configuration.from_bytes,
    "lmp.Accepted.from_bytes": lmp.Accepted.from_bytes,
    "lmp.NotAccepted.from_bytes": lmp.NotAccepted.from_bytes,
    "lmp.Features.from_bytes": lmp.Features.from_bytes,
    "lmp.AllowedBands.from_bytes": lmp.AllowedBands.from_bytes,
    "lmp.BandRanges.from_bytes": lmp.BandRanges.from_bytes,
    "hci.UllaSetup.from_bytes": hci.UllaSetup.from_bytes,
    "hci.ModeChangeEvent.from_bytes": hci.ModeChangeEvent.from_bytes,
    "sibling.parse_frame": sibling.parse_frame,
    "sibling.parse_start_session_request": sibling.parse_start_session_request,
    "describe.describe_aap": describe.describe_aap,
    "describe.describe_lmp": describe.describe_lmp,
    "describe.describe_hci_command": describe.describe_hci_command,
    "describe.describe_hci_event": describe.describe_hci_event,
    "capture.read_btsnoop": lambda blob: list(capture.read_btsnoop(blob)),
    "lmp.SessionBare.from_bytes": lmp.SessionBare.from_bytes,
    "lmp.PacketPermission.from_bytes": lmp.PacketPermission.from_bytes,
    "lmp.PacketTableRequest.from_bytes": lmp.PacketTableRequest.from_bytes,
    "lmp.LinkHalfword.from_bytes": lmp.LinkHalfword.from_bytes,
    "lmp.BandBlock.from_bytes": lmp.BandBlock.from_bytes,
    "lmp.SelectorRequest.from_bytes": lmp.SelectorRequest.from_bytes,
    "lmp.ValueAndCode.from_bytes": lmp.ValueAndCode.from_bytes,
    "avdtp.parse_header": avdtp.parse_header,
    "avdtp.parse_discover": avdtp.parse_discover,
    "avdtp.parse_capabilities": avdtp.parse_capabilities,
    "avdtp.parse_set_configuration_response": avdtp.parse_set_configuration_response,
}


def test_no_parser_raises_anything_but_a_protocol_error():
    rng = random.Random(20261019)
    for _ in range(600):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 24)))
        for name, parser in PARSERS.items():
            try:
                parser(data)
            except ProtocolError:
                continue
            except Exception as exc:
                raise AssertionError(
                    f"{name} raised {type(exc).__name__} on {data.hex(' ') or '(empty)'}: {exc}"
                ) from exc


def test_a_capture_truncated_anywhere_is_still_read_or_refused():
    """A capture cut off mid-record must not throw; it ends where it ends."""
    from test_capture import a_whole_session

    blob = a_whole_session()
    for cut in range(len(blob)):
        try:
            list(capture.read_btsnoop(blob[:cut]))
        except ProtocolError:
            continue


def test_the_describers_name_what_they_do_not_know():
    assert "not a type this protocol uses" in describe.describe_aap(bytes.fromhex("ff ff 04 00"))[0]
    assert "not one this protocol uses" in " ".join(
        describe.describe_aap(bytes.fromhex("04 00 04 00 99 00")))


def test_every_parser_in_the_package_is_in_the_list_above():
    """The fuzz list is only as good as its completeness, and nothing kept it complete.

    Eleven parsers had drifted out of it, which coverage noticed only indirectly: their
    malformed-input guards were the lines no test reached. A list maintained by hand needs a
    check that it is still the whole set, so this walks the package and compares. A parser is
    anything named `parse*` at module level or a `from_bytes` on a class - the two shapes this
    package uses for turning wire bytes into objects.

    If this fails, add the parser to PARSERS rather than adding it here.
    """
    import ast
    import pathlib

    package = pathlib.Path(__file__).resolve().parent.parent / "openulla"
    found = set()
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("parse"):
                found.add(f"{path.stem}.{node.name}")
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and member.name == "from_bytes":
                        found.add(f"{path.stem}.{node.name}.from_bytes")
    assert found, "the walk found no parsers at all, so it is not working"
    missing = sorted(name for name in found if name not in PARSERS)
    assert missing == [], f"these parsers are never fuzzed: {missing}"
