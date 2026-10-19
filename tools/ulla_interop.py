#!/usr/bin/env python3
"""Write or check everything in interop/: the bytes, the two state machines and the codes.

The point is an implementation in another language. It can read these files, encode the same
messages, walk the same transitions and name the same numbers, without reading a line of
Python; and it can feed each vector to its own decoder and compare against the description,
which is this package's own decode.

    python3 tools/ulla_interop.py            # print every file
    python3 tools/ulla_interop.py --write    # write them into interop/
    python3 tools/ulla_interop.py --check    # regenerate and diff, non-zero if they differ

Everything here comes from the library, which cites the firmware address or the measurement
behind it; nothing in this file is typed in by hand except the names and the section numbers.
"""
import argparse
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openulla import aap, hci, lmp, session, sibling
from openulla.codes import (
    FIRMWARE_NAMES,
    REASON_FIRMWARE_NAMES,
    RESULT_FIRMWARE_NAMES,
    AapReject,
    HciError,
    Mode,
    RejectReason,
    Result,
    SessionStatus,
)
from openulla.describe import DECODERS
from openulla.procedure import (
    HANDLER_ADDRESSES,
    SINK_HANDLER_ADDRESSES,
    THIRD_HANDLER_ADDRESSES,
    Procedure,
    SinkProcedure,
    ThirdProcedure,
)
from openulla.procedure import Event as ProcedureEvent

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRECTORY = os.path.join(HERE, "interop")
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
        ("lmp", "packet-table-apple", "5.13", lmp.PacketTableRequest(2).to_bytes()),
        ("lmp", "packet-table-edr", "5.13", lmp.PacketTableRequest(1).to_bytes()),
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




def session_rows():
    """The session manager's own table, one row per (state, event). [3.3]"""
    for row in session.TRANSITIONS:
        for event in row.events:
            yield (row.state.name, event.name, row.ok.name, row.other.name, row.handler)


def procedure_rows():
    """Every event each controller procedure accepts, and what it does with it. [5.9, 5.10]"""
    machines = (
        ("source", Procedure, HANDLER_ADDRESSES),
        ("sink", SinkProcedure, SINK_HANDLER_ADDRESSES),
        ("third", ThirdProcedure, THIRD_HANDLER_ADDRESSES),
    )
    for name, machine, addresses in machines:
        for state, handler in addresses.items():
            for event in ProcedureEvent:
                step = machine(state=state).feed(event)
                if not step.expected:
                    continue
                reported = (
                    f"mode {int(step.host_event.mode)} result {int(step.host_event.result)}"
                    if step.host_event is not None
                    else "-"
                )
                yield (
                    name,
                    state.name,
                    event.name,
                    step.after.name,
                    step.action.name,
                    "-" if step.reason is None else f"0x{step.reason:02x}",
                    reported,
                    handler,
                )


def code_rows():
    """Every named number of this protocol, with the firmware's own spelling where it has one."""
    groups = (
        ("mode", Mode, FIRMWARE_NAMES, "6"),
        ("result", Result, RESULT_FIRMWARE_NAMES, "6"),
        ("reject-reason", RejectReason, REASON_FIRMWARE_NAMES, "5.4"),
        ("session-status", SessionStatus, {}, "2.5"),
        ("aap-reject", AapReject, {}, "2.3"),
        ("hci-error", HciError, {}, "5.4"),
    )
    for group, enum, firmware, section in groups:
        for member in enum:
            yield (
                group,
                f"0x{int(member):04x}" if int(member) > 0xFF else f"0x{int(member):02x}",
                member.name,
                firmware.get(int(member), "-"),
                section,
            )


FILES = {
    "vectors.txt": (
        [
            "# openulla interop vectors, from the firmware build 9A348 and the measurements.",
            "#",
            "# One vector per line, tab separated: layer, name, the section of",
            "# docs/protocol.md that defines it, the bytes, and what this project's own",
            "# decoder makes of them. The layer and the name together address one vector.",
            "# Encode the same message in your own implementation and the bytes must match",
            "# byte for byte.",
            "#",
            "# layer\tname\tsection\thex\tdecode",
        ],
        lambda: (
            (layer, name, section, data.hex(" "),
             "; ".join(line.strip() for line in DECODERS[layer](data)))
            for layer, name, section, data in vectors()
        ),
    ),
    "session.txt": (
        [
            "# The earbud's session manager, one row per (state, event): the state it goes to",
            "# when the handler succeeds, the state it goes to otherwise, and the firmware",
            "# address of the handler itself. NOCHANGE means it stays where it is.",
            "# docs/protocol.md section 3.3 is the same table in prose.",
            "#",
            "# state\tevent\tnext-on-ok\tnext-otherwise\thandler",
        ],
        lambda: ((s, e, ok, other, f"rkof 0x{h:x}") for s, e, ok, other, h in session_rows()),
    ),
    "procedures.txt": (
        [
            "# The three procedures a controller runs, one per role, from docs/protocol.md 5.9",
            "# and 5.10. One row per event a state accepts; every other event leaves the state",
            "# where it is. 'reports' is the mode-change event the host is told, if any.",
            "#",
            "# role\tstate\tevent\tnext\taction\trefusal\treports\thandler",
        ],
        lambda: (
            (role, state, event, after, action, reason, reported, f"acib 0x{handler:x}")
            for role, state, event, after, action, reason, reported, handler in procedure_rows()
        ),
    ),
    "codes.txt": (
        [
            "# Every named number of this protocol, and the firmware's own spelling where the",
            "# image carries one ('-' where it does not). The section is where it is defined.",
            "#",
            "# group\tvalue\tname\tfirmware name\tsection",
        ],
        lambda: code_rows(),
    ),
}


def rendered(name):
    """One file, as the lines it should hold."""
    header, rows = FILES[name]
    return header + ["\t".join(str(field) for field in row) for row in rows()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help=f"write the files into {DIRECTORY}")
    ap.add_argument("--check", action="store_true", help="compare the files with the library")
    args = ap.parse_args()
    failed = 0
    for name in FILES:
        text = "\n".join(rendered(name)) + "\n"
        path = os.path.join(DIRECTORY, name)
        rows = len(rendered(name)) - len(FILES[name][0])
        if args.write:
            with open(path, "w", encoding="ascii") as handle:
                handle.write(text)
            print(f"wrote {rows} rows to interop/{name}")
            continue
        if args.check:
            with open(path, encoding="ascii") as handle:
                have = handle.read()
            if have == text:
                print(f"interop/{name}: {rows} rows, and the file matches the library")
                continue
            failed += 1
            print(f"interop/{name} and the library disagree:")
            for line in difflib.unified_diff(have.splitlines(), text.splitlines(),
                                             f"interop/{name}", "the library", lineterm=""):
                print(line)
            continue
        print(text, end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
