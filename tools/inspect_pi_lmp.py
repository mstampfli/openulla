"""Read Broadcom/Cypress LMP diagnostics in a BlueZ monitor btsnoop, offline.

Usage: python inspect_pi_lmp.py capture.btsnoop --peer <full-Bluetooth-address>
The JSON omits addresses, link keys and raw packets. A controller TX indication
is local evidence. A matching peer response adds evidence of peer reception.
Neither field measures RF frequency. See BlueZ monitor/broadcom.c and OpenULLA
docs/protocol.md sections 5.1, 5.3, 5.8 and 6.2 for the interpreted fields.
"""

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from pathlib import Path

HEADER = b"btsnoop\0" + struct.pack(">II", 1, 2001)
RECORD = struct.Struct(">IIIIQ")
BROADCOM_CYPRESS = {15, 305}


def address(value):
    parts = value.replace("-", ":").split(":")
    if len(parts) != 6 or any(not re.fullmatch("[0-9a-fA-F]{2}", p) for p in parts):
        raise argparse.ArgumentTypeError("expected a six-octet Bluetooth address")
    return bytes.fromhex("".join(parts))


def records(data):
    if data[:16] != HEADER:
        raise ValueError("expected BlueZ Linux monitor btsnoop version 1, datalink 2001")
    pos, number = 16, 0
    while pos < len(data):
        number += 1
        if len(data) - pos < RECORD.size:
            raise ValueError(f"short record header at frame {number}")
        original, size, flags, drops, timestamp = RECORD.unpack_from(data, pos)
        pos += RECORD.size
        if original != size or size > len(data) - pos:
            raise ValueError(f"incomplete record at frame {number}")
        if drops:
            raise ValueError(f"capture reports dropped packets at frame {number}")
        yield number, flags >> 16, flags & 0xFFFF, timestamp, data[pos : pos + size]
        pos += size


def is_lmp_diag(body):
    return len(body) == 63 and body[0] in (0, 1)


def inspect(path, peer):
    data = path.read_bytes()
    manufacturer, connections, pending, diagnostic_forms = {}, {}, {}, {}
    directions, opcodes, coverage = Counter(), Counter(), Counter()
    setup, responses, matches, features, masks = [], [], [], [], []
    acl_windows, frame, eligible = 0, 0, 0

    for frame, index, op, timestamp, body in records(data):
        links = connections.setdefault(index, {})
        if op in (0, 1, 9):  # new/deleted/closed controller
            links.clear()
            if op in (0, 1):
                manufacturer.pop(index, None)
        if op == 10 and len(body) >= 8:  # Index Info
            manufacturer[index] = int.from_bytes(body[6:8], "little")
        if op == 3 and len(body) >= 2 and body[1] == len(body) - 2:
            if len(body) == 14 and body[0] == 0x0E and body[3:6] == bytes.fromhex("01 10 00"):
                manufacturer[index] = int.from_bytes(body[10:12], "little")
            if len(body) == 6 and body[0] == 0x0E and body[3:6] == bytes.fromhex("03 0c 00"):
                links.clear()
            if len(body) == 13 and body[:3] == bytes.fromhex("03 0b 00"):
                handle = int.from_bytes(body[3:5], "little")
                links.pop(handle, None)
                if body[11] == 1:  # Classic ACL
                    remote = body[5:11][::-1]
                    links[handle] = (remote, frame)
                    acl_windows += remote == peer
            if len(body) == 6 and body[:3] == bytes.fromhex("05 04 00"):
                links.pop(int.from_bytes(body[3:5], "little"), None)
        if op == 3 and body[:3] == bytes.fromhex("ff 40 b4"):
            diag = body[3:]
        elif op == 11:
            diag = body
        else:
            continue
        if not is_lmp_diag(diag):
            continue
        if manufacturer.get(index) not in BROADCOM_CYPRESS:
            coverage["unverified_controller_diag"] += 1
            continue
        # Some recorders mirror a diagnostic, including its controller clock,
        # in both forms. Suppress only an identical, nearby cross-form copy.
        # All packets of either form remain usable when a recorder changes mode.
        identity = (index, diag)
        previous = diagnostic_forms.get(identity)
        if previous and previous[0] != op and 0 <= timestamp - previous[1] <= 20000:
            diagnostic_forms.pop(identity)
            coverage["mirrored_diagnostic"] += 1
            continue
        diagnostic_forms[identity] = (op, timestamp)
        eligible += 1
        if diag[5:9] != peer[-4:]:
            continue
        candidates = [link for link in links.values() if link[0][-4:] == diag[5:9]]
        if len(candidates) != 1 or candidates[0][0] != peer:
            coverage["unattributed_suffix"] += 1
            continue
        _, opened = candidates[0]
        sent = diag[0] == 0
        direction = "local_tx" if sent else "peer_rx_at_local"
        directions[direction] += 1
        pdu = diag[10 if sent else 13 :]
        opcode, tid = pdu[0] >> 1, pdu[0] & 1
        opcodes[f"0x{opcode:02x}"] += 1
        item = dict(frame=frame, connection_frame=opened, direction=direction, tid=tid)
        key = (index, opened, tid)
        if opcode == 0x73:
            # Vendor diagnostics pad the payload to 63 bytes and carry no LMP
            # length here. The protocol reference predicts nine bytes for 0x73.
            setup.append(
                dict(
                    **item, diagnostic_prefix_9_bytes=pdu[:9].hex(" "), on_air_length_verified=False
                )
            )
            if sent:
                pending.setdefault(key, []).append((frame, timestamp))
        elif opcode in (3, 4) and pdu[1] == 0x73:
            item["response"] = "accepted" if opcode == 3 else "not_accepted"
            if opcode == 4:
                item["reason"] = f"0x{pdu[2]:02x}"
            responses.append(item)
            if not sent:
                requests = pending.pop(key, [])
                if len(requests) == 1:
                    start, previous_time = requests[0]
                    matches.append(
                        dict(
                            request_frame=start,
                            response_frame=frame,
                            elapsed_us=timestamp - previous_time,
                            tid=tid,
                            response=item["response"],
                        )
                    )
                elif len(requests) > 1:
                    coverage["ambiguous_repeated_requests"] += 1
        elif opcode == 0x7B:
            ext = pdu[1]
            if ext in (0x0A, 0x0B):
                features.append(
                    dict(
                        **item,
                        message="request" if ext == 0x0A else "reply",
                        feature_word=f"0x{int.from_bytes(pdu[2:6], 'little'):08x}",
                    )
                )
            elif ext == 0x17:
                masks.append(
                    dict(
                        **item,
                        allowed_band_mask=f"0x{pdu[2]:02x}",
                        allows_unii_1=bool(pdu[2] & 0x02),
                        allows_unii_3=bool(pdu[2] & 0x04),
                    )
                )
    if directions:
        visibility = "target_lmp_diagnostics_present"
    elif eligible:
        visibility = "no_uniquely_attributed_target_lmp_diagnostics"
    else:
        visibility = "no_usable_broadcom_lmp_diagnostics"
    return dict(
        sha256=hashlib.sha256(data).hexdigest(),
        records=frame,
        captured_peer_acl_connections=acl_windows,
        visibility=visibility,
        broadcom_lmp_diagnostics=eligible,
        attributed_by_direction=dict(directions),
        attributed_opcodes=dict(sorted(opcodes.items())),
        coverage_notes=dict(coverage),
        setup_0x73=setup,
        responses_referencing_0x73=responses,
        matched_peer_responses_to_local_0x73=matches,
        apple_feature_exchange=features,
        advertised_band_masks=masks,
        lmp_length_source="fixed 63-byte diagnostic; 0x73 prefix uses expected 9-byte layout",
        rf_frequency_observable=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--peer", required=True, type=address)
    args = parser.parse_args()
    try:
        result = inspect(args.capture, args.peer)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"capture error: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
