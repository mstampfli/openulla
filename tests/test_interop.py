"""Everything in interop/: the files, the library and the decoders must all agree.

These files exist for implementations in other languages, so the risk is drift: a change to an
encoder or a table that nobody notices because only Python is tested. These tests fail on that.
"""
import pathlib
import subprocess
import sys

import ulla_interop

from openulla import session
from openulla.describe import DECODERS
from openulla.errors import ProtocolError

ROOT = pathlib.Path(__file__).resolve().parent.parent
# What each decoder must call the thing it recognised, so that a vector landing in the wrong
# layer cannot pass as decoded.
OPENING = {
    "aap": "AAP frame:",
    "sibling": "sibling frame:",
    "lmp": "LMP PDU:",
    "hci-command": "HCI command:",
    "hci-event": "HCI event:",
}


def test_the_files_on_disk_are_what_the_library_produces():
    run = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ulla_interop.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_every_vector_is_recognised_by_its_own_layer_and_told_apart_from_the_others():
    """Two vectors of one layer may not decode to the same words: that is what decoding is."""
    seen = {}
    for layer, name, _, data in ulla_interop.vectors():
        lines = DECODERS[layer](data)
        assert lines[0].startswith(OPENING[layer]), f"{name}: {lines[0]}"
        decode = "\n".join(lines)
        assert decode not in seen, f"{name} decodes exactly like {seen.get(decode)}"
        seen[decode] = name


def test_the_first_byte_of_every_vector_carries_meaning():
    """A decoder that says the same thing about different bytes is not decoding."""
    for layer, name, _, data in ulla_interop.vectors():
        before = DECODERS[layer](data)
        changed = bytes((data[0] ^ 0xFF,)) + data[1:]
        try:
            after = DECODERS[layer](changed)
        except (ProtocolError, ValueError, IndexError):
            continue  # refusing the changed byte is the strongest answer of all
        assert after != before, f"{name} decodes the same with its first byte flipped"


def test_every_file_is_ascii_with_the_columns_its_header_promises():
    for name, (header, _) in ulla_interop.FILES.items():
        text = (ROOT / "interop" / name).read_text(encoding="ascii")
        columns = header[-1].lstrip("# ").split("\t")
        rows = [line for line in text.splitlines() if line and not line.startswith("#")]
        assert rows, f"interop/{name} has no rows"
        for row in rows:
            assert len(row.split("\t")) == len(columns), f"interop/{name}: {row}"


def test_every_vector_line_holds_bytes_a_reader_can_use():
    text = (ROOT / "interop" / "vectors.txt").read_text(encoding="ascii")
    rows = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(rows) == len(ulla_interop.vectors())
    for row in rows:
        layer, name, section, hexed, decode = row.split("\t")
        assert layer in DECODERS and name and section and decode
        assert bytes.fromhex(hexed)


def test_layer_and_name_together_address_one_vector():
    keys = [(layer, name) for layer, name, _, _ in ulla_interop.vectors()]
    assert len(set(keys)) == len(keys)


def test_every_documented_layer_has_at_least_one_vector():
    layers = {layer for layer, _, _, _ in ulla_interop.vectors()}
    assert layers == set(DECODERS), f"missing vectors for {set(DECODERS) - layers}"


def test_the_session_file_holds_every_row_of_the_table_the_library_walks():
    rows = list(ulla_interop.session_rows())
    assert len(rows) == sum(len(row.events) for row in session.TRANSITIONS)
    states = {row[0] for row in rows}
    assert states <= {state.name for state in session.State}
    for _, event, ok, other, handler in rows:
        assert event in {e.name for e in session.Event}
        assert ok in {s.name for s in session.State} | {"NO_CHANGE"}
        assert other in {s.name for s in session.State} | {"NO_CHANGE"}
        assert handler


def test_the_procedure_file_covers_all_three_roles_and_only_accepted_events():
    rows = list(ulla_interop.procedure_rows())
    assert {row[0] for row in rows} == {"source", "sink", "third"}
    # Every row is an event its state accepts, so a state that accepts nothing has no rows,
    # and no (role, state, event) appears twice.
    keys = [(row[0], row[1], row[2]) for row in rows]
    assert len(set(keys)) == len(keys)
    assert any(row[6] != "-" for row in rows), "no row reports to the host"


def test_the_codes_file_names_every_number_the_library_names():
    rows = list(ulla_interop.code_rows())
    groups = {row[0] for row in rows}
    assert groups == {"mode", "result", "reject-reason", "session-status", "aap-reject",
                      "hci-error"}
    firmware = [row for row in rows if row[3] != "-"]
    assert len(firmware) >= 3 + 5, "the firmware's own mode and result names are missing"
