"""The reference's own tables, checked against the code that implements them.

docs/protocol.md and openulla/ have to agree, and tonight's corrections showed how easily a
table and a constant drift apart. tools/verify_claims.py checks the code against the firmware;
this checks the document against the code, so a change has to be made in both or the suite
says so.

The tables are read as markdown: rows of cells between pipes. Only the mechanical columns are
compared, the numbers and the firmware addresses; the prose columns are prose.
"""
import pathlib
import re

from openulla import aap, bands, lmp, session
from openulla.codes import RejectReason

DOC = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "protocol.md").read_text()


def section(number: str) -> str:
    """The text of one numbered section, up to the next heading of any depth."""
    match = re.search(rf"^#+ {re.escape(number)} .*?$(.*?)(?=^#+ )", DOC, re.M | re.S)
    assert match, f"section {number} is gone from protocol.md"
    return match.group(1)


def rows(text: str) -> list[list[str]]:
    """Every data row of every table in that text, as lists of stripped cells."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- :"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and cells[0].lower() in ("offset", "bit", "state", "value", "extended opcode",
                                          "escape", "check", "mode", "command", "step", "#"):
            continue  # a header row
        out.append(cells)
    return out


def numbers(cell: str) -> list[int]:
    """Every integer in a cell, decimal or hex."""
    return [int(x, 16) if x.lower().startswith("0x") else int(x)
            for x in re.findall(r"0x[0-9a-fA-F]+|\d+", cell)]


def expand(cell: str) -> set[int]:
    """A cell like "0x01, 0x02, 0x0A to 0x11" as a set of numbers."""
    found: set[int] = set()
    for part in cell.split(","):
        ends = numbers(part)
        if " to " in part and len(ends) == 2:
            found.update(range(ends[0], ends[1] + 1))
        else:
            found.update(ends)
    return found


def test_the_band_table_in_6_3_is_the_table_in_the_code():
    table = [r for r in rows(section("6.3")) if len(r) >= 7 and numbers(r[0])]
    assert len(table) == bands.BAND_COUNT
    for cells in table:
        index = numbers(cells[0])[0]
        band = bands.band(index)
        if not band.defined:
            assert "zero" in cells[1].lower()
            continue
        assert numbers(cells[1])[0] == band.start_mhz, index
        assert numbers(cells[2])[0] == band.width_mhz, index
        assert numbers(cells[3])[0] == band.channels, index
        assert numbers(cells[4])[0] == band.channel_bandwidth_mhz, index
        assert numbers(cells[5])[0] == band.spacing_mhz, index
        assert numbers(cells[6])[0] == band.first_channel, index


def test_the_transition_table_in_3_4_is_the_table_in_the_code():
    expected = {
        (int(row.state), int(event), row.handler)
        for row in session.TRANSITIONS
        for event in row.events
    }
    documented = set()
    for cells in rows(section("3.4")):
        if len(cells) < 5 or not numbers(cells[0]) or not numbers(cells[4]):
            continue
        state = numbers(cells[0])[0]
        handler = numbers(cells[4])[-1]
        for event in numbers(cells[1]):
            documented.add((state, event, handler))
    assert documented, "section 3.4 has no readable rows any more"
    assert documented == expected


def test_the_reason_values_in_5_4_are_the_ones_the_code_knows():
    documented = set()
    for cells in rows(section("5.4")):
        if len(cells) == 2 and numbers(cells[0]) and not cells[1].startswith("0x"):
            documented.add(numbers(cells[0])[0])
    assert documented == {0} | {reason.value for reason in RejectReason}


def test_the_escape_inventories_in_5_6_are_the_sets_in_the_code():
    implemented, refused = {}, {}
    for cells in rows(section("5.6")):
        if len(cells) != 3 or cells[0] not in ("0x7B", "0x7F"):
            continue
        implemented[cells[0]] = expand(cells[1])
        refused[cells[0]] = expand(cells[2])
    assert implemented["0x7B"] == set(lmp.ESCAPE_7B_IMPLEMENTED)
    assert refused["0x7B"] == set(lmp.ESCAPE_7B_REFUSED)
    assert implemented["0x7F"] == set(lmp.ESCAPE_7F_IMPLEMENTED)
    assert refused["0x7F"] == set(lmp.ESCAPE_7F_REFUSED)


def test_the_commands_in_2_4_are_the_opcodes_in_the_code():
    documented = set()
    for cells in rows(section("2.4")):
        if len(cells) == 3 and cells[0].startswith("0x"):
            documented.add(numbers(cells[0])[0])
    assert documented == {int(op) for op in aap.Opcode} - {int(aap.Opcode.STATUS)}


def test_every_section_the_tests_read_is_still_there():
    for number in ("2.4", "3.4", "5.4", "5.6", "6.3"):
        assert rows(section(number)), number
