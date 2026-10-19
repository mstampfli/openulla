"""The documentation quotes this code's own output, so the two must not drift apart."""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def demo_output():
    run = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ulla_demo.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout


def fenced_block(text, starts_with):
    """The fenced block whose first line contains `starts_with`."""
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        if starts_with in block.splitlines()[0]:
            return block.splitlines()
    raise AssertionError(f"no fenced block starting with {starts_with!r}")


def test_the_readme_transcript_is_what_the_demo_prints():
    lines = fenced_block((ROOT / "README.md").read_text(), "tools/ulla_demo.py")
    output = demo_output()
    for line in lines[1:]:
        if line.strip():
            assert line in output, line


def test_the_implementation_doc_transcript_is_what_the_demo_prints():
    lines = fenced_block((ROOT / "docs" / "implementation.md").read_text(), "the host opens AAP")
    output = demo_output()
    for line in lines:
        if line.strip():
            assert line in output, line


def test_the_verifier_sample_names_every_check_it_really_has():
    import verify_claims  # the tools directory is on the path through conftest.py

    doc = (ROOT / "docs" / "implementation.md").read_text()
    quoted = re.findall(r"^  PASS  (\S.*?)\s{2,}", doc, re.M)
    names = [name for name, _ in verify_claims.CHECKS]
    assert quoted, "the implementation doc no longer shows a verifier run"
    assert quoted == names
    assert f"{len(names)} of {len(names)} claims hold" in doc


# The size of each payload of build 9A348, as its own FTAB entry gives it
# (`tools/ftab.py ... list --payload FTAB`). A citation past the end of its payload is a typo,
# and a dropped digit is the easy way to make one.
PAYLOAD_SIZES = {"acib": 1066208, "rkof": 2088320, "sbd1": 1462016, "ctab": 5898277}
RAM_WINDOW = 0x10000000  # addresses at or above this are the controller's RAM, not offsets


def test_every_firmware_citation_is_inside_its_payload():
    """Every address attributed to a payload must be inside it.

    A citation often names one payload and then several addresses, as "acib 0xA, 0xB and 0xC",
    so after a payload token this takes **every** address up to the next payload token or the
    end of the citation rather than only the first. The earlier version took only the first,
    which left the rest of each list unchecked: 388 addresses were being matched and three of
    the landmarks of 5.13 were not among them.
    """
    token = re.compile(r"\b(acib|rkof|sbd1|ctab)\b|(0x[0-9a-fA-F]{3,})")
    adjacent = re.compile(r"\b(acib|rkof|sbd1|ctab)`?\s+(0x[0-9a-fA-F]{3,})")
    cited = 0

    def one(doc_name, payload, text_address):
        nonlocal cited
        address = int(text_address, 0)
        if address >= RAM_WINDOW:
            return False
        cited += 1
        assert address < PAYLOAD_SIZES[payload], f"{doc_name}: {payload} {text_address}"
        return True

    for doc in [*sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md"]:
        text = doc.read_text()
        # Inside a bracketed citation a payload is named once and several addresses follow it,
        # so the payload carries across them until a RAM or aperture address ends the run.
        for span in re.findall(r"\[fw [^\]]*\]", text):
            payload = None
            for match in token.finditer(span):
                if match.group(1):
                    payload = match.group(1)
                elif payload is not None and not one(doc.name, payload, match.group(2)):
                    payload = None
        # Outside one, only an address **immediately** after a payload token is a citation.
        # Carrying there swallows unrelated numbers, and it did twice while this was written: a
        # report class of 0x00200000 after an rkof mention, and each entry of a list of stream
        # positions pairing with the name of the entry before it.
        for match in adjacent.finditer(re.sub(r"\[fw [^\]]*\]", " ", text)):
            one(doc.name, match.group(1), match.group(2))
    assert cited > 500, f"only {cited} citations found; the pattern stopped matching"


def test_the_protocol_reference_has_a_table_of_contents_that_matches_its_headings():
    """A reference nobody can navigate is a worse reference, and a stale index is worse still."""
    text = (ROOT / "docs" / "protocol.md").read_text()
    wanted = []
    for line in text.splitlines():
        heading = re.match(r"^(#{2,3}) (.+)$", line)
        if not heading or heading.group(2) == "Contents":
            continue
        title = heading.group(2)
        slug = re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", title.lower()).strip())
        wanted.append(f"{'  ' * (len(heading.group(1)) - 2)}- [{title}](#{slug})")
    contents = text.split("## Contents\n", 1)[1].split("\n## ", 1)[0]
    have = [line for line in contents.splitlines() if line.strip()]
    assert have == wanted, (
        "the table of contents is stale; the headings now want:\n" + "\n".join(wanted)
    )


def test_the_escape_space_inventory_covers_every_opcode_it_implements():
    """Section 5.15 claims to be the whole 0x7B space, so it must be [protocol.md 5.15].

    The implemented set comes from `openulla/lmp.py`, which the verifier checks against the
    firmware's own dispatch table, so this ties the prose to the image through two links rather
    than restating a list by hand.
    """
    sys.path.insert(0, str(ROOT))
    from openulla import lmp

    doc = (ROOT / "docs" / "protocol.md").read_text()
    section = doc.split("### 5.15")[1].split("\n###")[0]
    listed = set()
    for row in re.findall(r"^\| (0x[0-9A-Fa-f, x]+) \|", section, re.M):
        for item in row.split(","):
            item = item.strip()
            if item.startswith("0x"):
                listed.add(int(item, 16))
    implemented = set(lmp.ESCAPE_7B_IMPLEMENTED)
    assert listed == implemented, {
        "listed but not implemented": sorted(hex(x) for x in listed - implemented),
        "implemented but not listed": sorted(hex(x) for x in implemented - listed),
    }


def test_the_citation_check_catches_what_it_is_for():
    """A guard that cannot fail is not a guard, and this one had a blind spot worth pinning.

    The shapes below are the ones that mattered while it was written: an address that is wrong
    but not first in its list, which the earlier pattern never looked at, and two shapes that
    broke intermediate versions by pairing a payload name with a number that is not an offset
    into it.
    """
    token = re.compile(r"\b(acib|rkof|sbd1|ctab)\b|(0x[0-9a-fA-F]{3,})")
    adjacent = re.compile(r"\b(acib|rkof|sbd1|ctab)`?\s+(0x[0-9a-fA-F]{3,})")

    def flagged(text):
        bad = []
        for span in re.findall(r"\[fw [^\]]*\]", text):
            payload = None
            for match in token.finditer(span):
                if match.group(1):
                    payload = match.group(1)
                elif payload is not None:
                    value = int(match.group(2), 0)
                    if value >= RAM_WINDOW:
                        payload = None
                    elif value >= PAYLOAD_SIZES[payload]:
                        bad.append(match.group(2))
        for match in adjacent.finditer(re.sub(r"\[fw [^\]]*\]", " ", text)):
            value = int(match.group(2), 0)
            if value < RAM_WINDOW and value >= PAYLOAD_SIZES[match.group(1)]:
                bad.append(match.group(2))
        return bad

    assert flagged("[fw acib 0xdeadbe the thing]") == ["0xdeadbe"]
    assert flagged("[fw acib 0xf11a0, 0xdeadbe, 0xf120c]") == ["0xdeadbe"]
    assert flagged("[fw acib 0xf11a0 and 0xdeadbe]") == ["0xdeadbe"]
    assert flagged("[fw acib 0xf11a0, 0xf11e8, 0xf120c]") == []
    # A list of positions in another file, written address-first for exactly this reason.
    assert flagged("0x18cd30 for `acib`, 0x198b42 for `phyb`") == []
    # A value that is not an address at all, sitting after a payload name in prose.
    assert flagged("the rkof image, class 0x00200000") == []
