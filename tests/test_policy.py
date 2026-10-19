"""What may be published, checked against what is tracked.

The publishing rules this project works under are short: our own prose and our own code, no
Apple material, and no capture that could carry a link key or somebody else's address. Those
are judgement calls in general, but three of them are mechanical, and a contributor's first
mistake would be one of the three. CI runs this.
"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The only device addresses that may appear: documentation placeholders and the all-zero one.
PLACEHOLDERS = {"aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "00:00:00:00:00:00"}
ADDRESS = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
# A line of somebody else's disassembly looks like this; our own prose quotes instructions
# inline instead, without an address column.
LISTING = re.compile(r"^0x[0-9a-f]{6}: ", re.M)
# House style for everything published here is plain ASCII punctuation. Em and en dashes look
# fine in a browser and wrong in a terminal, a patch and a commit message, and nine of them had
# already reached two documents before this check existed. The two characters are built with
# chr() for the same reason the example address below is: written out, they would be the very
# thing this file forbids, and the check would rightly refuse the file that holds it.
DASHES = (chr(0x2013), chr(0x2014))
TYPOGRAPHY = re.compile("[%s%s]" % DASHES)
FORBIDDEN_SUFFIXES = (".bin", ".uarp", ".btsnoop", ".pklg", ".pcap", ".strings", ".zip", ".pdf")


def tracked():
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [ROOT / name for name in listed.stdout.split()]


def test_no_tracked_file_is_apple_firmware_or_a_capture():
    for path in tracked():
        assert not path.name.endswith(FORBIDDEN_SUFFIXES), path.name


def test_no_tracked_file_carries_a_real_device_address():
    for path in tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for found in ADDRESS.findall(text):
            assert found.lower() in PLACEHOLDERS, f"{path.name}: {found}"


def test_no_tracked_file_is_a_disassembly_listing():
    for path in tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        lines = LISTING.findall(text)
        assert len(lines) <= 2, f"{path.name} holds {len(lines)} listing-shaped lines"


def test_no_tracked_file_uses_an_em_or_en_dash():
    for path in tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        found = TYPOGRAPHY.findall(text)
        assert not found, f"{path.name} holds {len(found)} em or en dashes"


def test_the_checks_themselves_catch_what_they_are_for():
    """A guard that cannot fail is not a guard: these are the shapes it must reject.

    The example address is assembled at run time on purpose. Written out, it would be an
    address in a tracked file, and the check above would rightly refuse this very file: that
    is what happened the first time, because the test was still untracked when it was proved.
    """
    example = ":".join(("60", "f4", "45", "11", "22", "33"))
    assert ADDRESS.findall(f"the device at {example} answered")
    assert example not in PLACEHOLDERS
    assert LISTING.findall("0x0661ab: movs r0, #1\n0x0661ad: bx lr\n")
    assert "acib.bin".endswith(FORBIDDEN_SUFFIXES)
    assert TYPOGRAPHY.findall("a sentence %s interrupted %s twice" % DASHES)
