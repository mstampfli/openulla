"""The interop vectors: the file, the library and the decoders must all say the same thing.

The file exists for implementations in other languages, so the risk is drift: a change to an
encoder that nobody notices because only Python is tested. These tests fail on that.
"""
import pathlib
import subprocess
import sys

import ulla_vectors

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


def test_the_file_on_disk_is_what_the_library_produces():
    run = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ulla_vectors.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_every_vector_is_recognised_by_its_own_layer_and_told_apart_from_the_others():
    """Two vectors of one layer may not decode to the same words: that is what decoding is."""
    seen = {}
    for layer, name, _, data in ulla_vectors.vectors():
        lines = DECODERS[layer](data)
        assert lines[0].startswith(OPENING[layer]), f"{name}: {lines[0]}"
        decode = "\n".join(lines)
        assert decode not in seen, f"{name} decodes exactly like {seen.get(decode)}"
        seen[decode] = name


def test_the_first_byte_of_every_vector_carries_meaning():
    """A decoder that says the same thing about different bytes is not decoding."""
    for layer, name, _, data in ulla_vectors.vectors():
        before = DECODERS[layer](data)
        changed = bytes((data[0] ^ 0xFF,)) + data[1:]
        try:
            after = DECODERS[layer](changed)
        except (ProtocolError, ValueError, IndexError):
            continue  # refusing the changed byte is the strongest answer of all
        assert after != before, f"{name} decodes the same with its first byte flipped"


def test_the_file_is_ascii_and_tab_separated():
    text = (ROOT / "interop" / "vectors.txt").read_text(encoding="ascii")
    rows = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(rows) == len(ulla_vectors.vectors())
    for row in rows:
        layer, name, section, hexed, decode = row.split("\t")
        assert layer in DECODERS and name and section and decode
        assert bytes.fromhex(hexed)


def test_layer_and_name_together_address_one_vector():
    keys = [(layer, name) for layer, name, _, _ in ulla_vectors.vectors()]
    assert len(set(keys)) == len(keys)


def test_every_documented_layer_has_at_least_one_vector():
    layers = {layer for layer, _, _, _ in ulla_vectors.vectors()}
    assert layers == set(DECODERS), f"missing vectors for {set(DECODERS) - layers}"
