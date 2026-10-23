"""The contributed capture records, and the docs and constants that quote them.

`evidence/` holds sanitized summaries of captures whose raw files stayed with the contributor,
so the numbers in them are the only form of those observations this repository has. Three
things can drift: a summary can stop adding up, a record count can be retyped into the prose
wrong, and the firmware-side opcode tables of `tools/verify_claims.py` can stop covering the
opcodes the captures actually contain. Each of those is checked here rather than trusted.
"""
import json
import pathlib

import verify_claims  # the tools directory is on the path through conftest.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = sorted((ROOT / "evidence").glob("*/summary.json"))


def summaries() -> list[dict]:
    return [json.loads(path.read_text()) for path in RUNS]


def test_there_are_evidence_runs_to_check():
    assert RUNS, "evidence/ has no summary.json, so every check below would pass vacuously"


def test_each_run_adds_up_to_its_own_total():
    for path, run in zip(RUNS, summaries(), strict=True):
        capture = run["capture"]
        counted = sum(capture["record_types"].values())
        assert counted == capture["records"], f"{path.parent.name}: {counted} record types"
        assert sum(capture["vendor_commands"].values()) <= capture["record_types"]["hci_command"]
        requested = run["selected_command_observations"]["requested_opcode_counts"]
        for opcode, count in requested.items():
            seen = capture["vendor_commands"].get(opcode, 0)
            assert seen == count, f"{path.parent.name}: {opcode} counted twice differently"


def test_the_windows_account_for_every_synchronous_record():
    """Where a run splits itself into phases, the phases have to cover the file's own SCO total."""
    for path, run in zip(RUNS, summaries(), strict=True):
        windows = run.get("controlled_windows")
        if not windows:
            continue
        types = run["capture"]["record_types"]
        whole = types.get("sco_received", 0) + types.get("sco_sent", 0)
        phased = sum(
            count
            for window in windows
            for kind, count in window["record_types"].items()
            if kind.startswith("sco")
        )
        assert phased == whole, f"{path.parent.name}: {phased} of {whole} SCO records in phases"
        for window in windows:
            assert sum(window["record_types"].values()) == window["record_count"]


def test_the_firmware_tables_cover_every_opcode_the_captures_hold():
    """The union of what the host was seen sending is exactly what 6.8 accounts for.

    This is the join between someone else's capture and our own reading of the image: an opcode
    appearing in a new run has to be looked up in the earbuds' table and recorded as present or
    absent, and this fails until it is.
    """
    observed = set()
    for run in summaries():
        observed |= {int(opcode, 16) for opcode in run["capture"]["vendor_commands"]}
    accounted = set(verify_claims.SHARED_HOST_COMMANDS) | set(verify_claims.ABSENT_HOST_COMMANDS)
    assert observed == accounted, (
        f"captured but unaccounted: {sorted(hex(op) for op in observed - accounted)}; "
        f"accounted but never captured: {sorted(hex(op) for op in accounted - observed)}"
    )


def test_the_prose_quotes_the_record_counts_the_summaries_give():
    """A preserved run has to be a run the documents actually account for.

    Only each run's own count is checked. A sum across runs is meaningful for two captures from
    one host and would stop being meaningful the moment a third host appears, so nothing here
    asks for one.
    """
    docs = "".join(
        (ROOT / name).read_text() for name in ("README.md", "docs/ulla.md", "docs/testing.md")
    )
    for path, run in zip(RUNS, summaries(), strict=True):
        count = run["capture"]["records"]
        assert f"{count:,}" in docs, f"{path.parent.name}: {count:,} records is in no document"
