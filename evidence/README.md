# Contributor evidence

This directory preserves sanitized observations reported in the issue tracker.
Each run has a report and a machine-readable summary. These records describe what
was observed; a missing command does not establish that a host lacks ULLA support.

| Run | Scope | Records | Report |
| --- | --- | --- | --- |
| 2026-10-18, M1 / Broadcom | Microphone transitions within an established AirPods connection | 18,623 | [Report](2026-10-18-m1-airpods-established-session/README.md) |
| 2026-10-19, M1 / Broadcom | Normal AirPods reconnection and playback | 23,767 | [Report](2026-10-19-m1-airpods-reconnection/README.md) |

## What is preserved

The summaries include hardware/software versions, complete record-type and vendor
opcode counts, selected command lengths, the first `0xFD02` parameter, its response
status, and relative event timing. Raw-file SHA-256 hashes and byte sizes identify
the private source files. Record numbers are one-based indexes into those files.
Setup details and local peer-address matches are contributor-reported observations.

No device names, addresses, serial numbers, keys, arbitrary command/event payloads,
audio samples, firmware binaries or raw system logs are included. Raw files remain
with the contributor. These summaries are not independently replayable captures;
the hashes identify a source but do not make its contents publicly verifiable.

The two files contain controller-wide traffic. Aggregate counts are not attributed
entirely to the AirPods. PacketLogger marked 17 and 7 items, respectively, as
errors; those causes remain uninvestigated. Successful parsing does not rule out
loss elsewhere in the capture pipeline.

Vendor opcodes can mean different things on the Mac's Broadcom controller and the
earbuds' H2 controller. In particular, the first `0xFD02` parameter below is an
observed byte, not a verified interpretation of the Mac's packet-size setting.
CoreAudio endpoint sample rates also do not establish a wireless codec or band.

## Validation and time reference

The private PacketLogger files were parsed offline with a contributor's
standard-library parser and independently checked with Wireshark `tshark`.
Record-type counts, the first `0xFD02` parameter and absence of the requested
opcodes were checked against the private files. The parser is not part of this
evidence-only contribution.

All JSON times are milliseconds relative to the first record, with separate fields
for differences between named events. A positive `nearest_relative_to_fd02_ms`
means after `0xFD02`; a negative value means before it. Full-file timestamp spans
include prelude traffic and are not the durations of the controlled windows.

In the first file, saved absolute timestamps behaved like local wall-clock time
when interpreted as Unix seconds. External phase markers were aligned using the
local offset, corroborated by synchronous connection setup and teardown. No
absolute packet timestamps or device handles are published.

Someone holding a source file can independently check counts and opcode presence:

```sh
# Set capture to a private source file; do not commit it or its payloads.
tshark -r "$capture" -T fields -e packetlogger.type | sort | uniq -c
tshark -r "$capture" -Y 'bthci_cmd.opcode == 0xfd02' \
  -T fields -e frame.number -e bthci_cmd.opcode
tshark -r "$capture" -Y 'btlmp' -T fields -e frame.number
```

To examine just the requested first parameter without printing the rest:

```sh
tshark -r "$capture" -Y 'bthci_cmd.opcode == 0xfd02' -T jsonraw |
python3 -c 'import json, sys
for packet in json.load(sys.stdin):
    raw = bytes.fromhex(packet["_source"]["layers"]["bthci_cmd_raw"][0])
    assert len(raw) == 3 + raw[2] and raw[2] > 0
    print("parameter_length:", raw[2], "first_parameter_hex:", raw[3:4].hex())'
```

New evidence should state its source, tested conditions, exclusions and limitations.
Follow the repository's capture-sharing rules before adding any other fields.
