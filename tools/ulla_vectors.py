#!/usr/bin/env python3
"""Write or check the interop vectors: every message this project documents, as bytes.

The point is an implementation in another language. It can read interop/vectors.txt, encode
the same message, and compare, without reading a line of Python; and it can feed each vector
to its own decoder and compare against the description, which is this package's own decode.

    python3 tools/ulla_vectors.py            # print them
    python3 tools/ulla_vectors.py --write    # write interop/vectors.txt
    python3 tools/ulla_vectors.py --check    # regenerate and diff, non-zero if they differ

Every byte here comes from the library, which cites the firmware address or the measurement
behind it; nothing in this file is typed in by hand except the names and the section numbers.
"""
import argparse
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import aap, hci, lmp, sibling
from openulla.codes import Mode, RejectReason, Result, SessionStatus
from openulla.describe import DECODERS

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "interop",
                    "vectors.txt")
PEER = bytes.fromhex("112233445566")  # a host address, in the order the stack holds it
HANDLE = 0x000B  # the connection handle the measured run used
SETUP = hci.UllaSetup(handle=HANDLE, extra_flags=0, config_word=0x0100, flags=0x03)
BANDS = 0xE6  # the mask the demo uses: bands 1, 2, 5, 6 and 7


def vectors():
    """Every vector, as (layer, name, section, bytes). The order is the order of the document."""
    return [
        ("aap", "open-request", "2.2", aap.open_request()),
        ("aap", "open-reply", "2.2", aap.open_reply(1, 3)),
        ("aap", "start-session-mode-0", "2.4", aap.start_session(0)),
        ("aap", "start-session-mode-1", "2.4", aap.start_session(1)),
        ("aap", "status-ok", "2.5", aap.status_reply(SessionStatus.OK)),
        ("aap", "status-sibling-not-ready", "2.5",
         aap.status_reply(SessionStatus.SIBLING_TRANSPORT_NOT_READY)),
        ("aap", "subscribe-reports", "2.6", aap.subscribe_reports()),
        ("aap", "start-stream", "2.4", aap.start_stream()),
        ("aap", "stop-stream", "2.4", aap.stop_stream()),
        ("aap", "stop-session", "2.4", aap.stop_session()),
        ("aap", "abort-response", "2.4", aap.abort_response()),
        ("aap", "configure-statistics", "2.4", aap.configure_statistics(1, 0x20, 0)),
        ("sibling", "start-session-request", "4", sibling.start_session_request(PEER, 0)),
        ("sibling", "start-session-response", "4",
         sibling.empty(sibling.Message.START_SESSION_RSP)),
        ("sibling", "dsp-config-complete", "4", sibling.empty(sibling.Message.DSP_CONFIG_COMPLETE)),
        ("sibling", "configure-statistics", "4", sibling.configure_statistics(1, 0x20, 0)),
        ("lmp", "features-request", "5.8", lmp.Features(0x60).to_bytes()),
        ("lmp", "features-reply", "5.8", lmp.Features(0x60, reply=True).to_bytes()),
        ("lmp", "allowed-bands", "6.2", lmp.AllowedBands(BANDS).to_bytes()),
        ("lmp", "band-ranges", "6.5", lmp.BandRanges(
            [lmp.BandRange(1, 0, 4), lmp.BandRange(2, 0, 5), lmp.BandRange(5, 1, 3)]
        ).to_bytes()),
        ("lmp", "setup-request", "5.1", lmp.SetupRequest(
            radio_value=BANDS, config_word=SETUP.config_word, flags=SETUP.flags,
            extra_flags=SETUP.extra_flags).to_bytes()),
        ("lmp", "accepted", "5.4", lmp.Accepted(lmp.OPCODE_ULLA_SETUP).to_bytes()),
        ("lmp", "not-accepted-busy", "5.4", lmp.NotAccepted(
            lmp.OPCODE_ULLA_SETUP, RejectReason.PRIMARY_DISALLOWED_ACTIVITY).to_bytes()),
        ("lmp", "configuration-0x74", "5.2", lmp.Configuration(0xE8, 0).to_bytes()),
        ("lmp", "configuration-0x75", "5.2",
         lmp.Configuration(0xE8, 0, opcode=lmp.OPCODE_ULLA_CONFIG_ALT).to_bytes()),
        ("lmp", "selector-request", "5.11", lmp.SelectorRequest(1).to_bytes()),
        ("lmp", "value-and-code", "5.11", lmp.ValueAndCode(0x15, 5).to_bytes()),
        ("lmp", "value-and-code-second-pair", "5.11", lmp.ValueAndCode(0x2A, 10).to_bytes()),
        ("lmp", "session-bare-0x76", "5.2", lmp.SessionBare().to_bytes()),
        ("hci-command", "band-gate-on", "6.1", hci.band_gate(True)),
        ("hci-command", "feature-group-on", "5.8", hci.feature_group(True)),
        ("hci-command", "allowed-bands-first", "6.1", hci.allowed_bands(BANDS)[0]),
        ("hci-command", "allowed-bands-second", "6.1", hci.allowed_bands(BANDS)[1]),
        ("hci-command", "ulla-setup", "6", hci.command(hci.OPCODE_ULLA_SETUP, SETUP.to_bytes())),
        ("hci-command", "read-link-band", "6.1", hci.read_link_band(HANDLE)),
        ("hci-command", "set-link-band", "6.1", hci.set_link_band(HANDLE, 2)),
        ("hci-command", "band-ranges", "6.5", hci.band_ranges([(1, 0, 4), (2, 0, 5)])),
        ("hci-command", "country-code", "6.4", hci.country_code(2)),
        ("hci-command", "stream-dma-address", "7.1", hci.stream_dma_address(0x10044000)),
        ("hci-command", "stream-start", "7.1", hci.stream_start()),
        ("hci-command", "stream-stop", "7.1", hci.stream_stop()),
        ("hci-command", "read-stream-descriptor", "7.1", hci.read_stream_descriptor()),
        ("hci-command", "session-byte-0xfe7b", "6.6", hci.session_command(0xFE7B, bytes((1,)))),
        ("hci-command", "session-value-0xfe16", "6.6",
         hci.session_command(0xFE16, bytes((1,)) + (1000).to_bytes(4, "little"))),
        ("hci-event", "mode-on-success", "6.0",
         hci.ModeChangeEvent(HANDLE, Mode.ULLA_ON, Result.SUCCESS,
                             RejectReason.NOT_APPLICABLE).to_bytes()),
        ("hci-event", "mode-off-success", "6.0",
         hci.ModeChangeEvent(HANDLE, Mode.BT_ACL_ACTIVE, Result.SUCCESS,
                             RejectReason.NOT_APPLICABLE).to_bytes()),
        ("hci-event", "mode-refused-by-peer", "5.4",
         hci.ModeChangeEvent(HANDLE, Mode.BT_ACL_ACTIVE, Result.SETUP_REJECTED_BY_PEER,
                             RejectReason.PRIMARY_DISALLOWED_ACTIVITY).to_bytes()),
    ]


def rendered():
    """The whole file, as the lines it should hold."""
    lines = [
        "# openulla interop vectors, from the firmware build 9A348 and the measurements.",
        "#",
        "# One vector per line, tab separated: layer, name, the section of docs/protocol.md",
        "# that defines it, the bytes, and what this project's own decoder makes of them.",
        "# Generated by tools/ulla_vectors.py; `--check` fails if the library and this file",
        "# disagree, so neither can drift. Encode the same message in your own implementation",
        "# and the bytes must match byte for byte.",
        "#",
        "# layer\tname\tsection\thex\tdecode",
    ]
    for layer, name, section, data in vectors():
        decode = "; ".join(line.strip() for line in DECODERS[layer](data))
        lines.append(f"{layer}\t{name}\t{section}\t{data.hex(' ')}\t{decode}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help=f"write {PATH}")
    ap.add_argument("--check", action="store_true", help="compare the file with the library")
    args = ap.parse_args()
    text = "\n".join(rendered()) + "\n"
    if args.write:
        with open(PATH, "w", encoding="ascii") as handle:
            handle.write(text)
        print(f"wrote {len(vectors())} vectors to {PATH}")
        return 0
    if args.check:
        with open(PATH, encoding="ascii") as handle:
            have = handle.read()
        if have == text:
            print(f"{len(vectors())} vectors, and the file matches the library")
            return 0
        print("the file and the library disagree:")
        for line in difflib.unified_diff(have.splitlines(), text.splitlines(),
                                         "interop/vectors.txt", "the library", lineterm=""):
            print(line)
        return 1
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
