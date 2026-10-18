"""Make the package and the tools importable however pytest is invoked.

Without this, running the suite from anywhere but the repository root fails to import
openulla at all, which is a thing a contributor does by accident on the first try.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

for path in (ROOT, ROOT / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
