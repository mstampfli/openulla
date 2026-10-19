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
    cited = 0
    for doc in [*sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md"]:
        text = doc.read_text()
        for match in re.finditer(r"\b(acib|rkof|sbd1|ctab)\s+(0x[0-9a-fA-F]+)", text):
            payload, address = match.group(1), int(match.group(2), 0)
            if address >= RAM_WINDOW:
                continue
            cited += 1
            assert address < PAYLOAD_SIZES[payload], f"{doc.name}: {payload} {match.group(2)}"
    assert cited > 300, f"only {cited} citations found; the pattern stopped matching"


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
