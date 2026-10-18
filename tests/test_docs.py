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
    sys.path.insert(0, str(ROOT / "tools"))
    import verify_claims

    doc = (ROOT / "docs" / "implementation.md").read_text()
    quoted = re.findall(r"^  PASS  (\S.*?)\s{2,}", doc, re.M)
    names = [name for name, _ in verify_claims.CHECKS]
    assert quoted, "the implementation doc no longer shows a verifier run"
    assert quoted == names
    assert f"{len(names)} of {len(names)} claims hold" in doc
