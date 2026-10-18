"""Every parser in this package answers random bytes with a ProtocolError, never a crash.

A decoder gets pointed at captures and logs, so the contract that matters is that malformed
input is refused in one recognisable way. ProtocolError is a ValueError, so a caller can
catch either; what must not happen is an IndexError, a struct error or a bare ValueError from
an enum deep inside.
"""
import random

from openulla import aap, capture, describe, hci, lmp, sibling
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
            except Exception as exc:  # noqa: BLE001 - that is the point of the test
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
