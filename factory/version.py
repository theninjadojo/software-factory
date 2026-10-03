"""The running version: the VERSION file at the root of the install (copied into the image), else 'dev'."""
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def current() -> str:
    try:
        v = _FILE.read_text().strip()
    except OSError:
        return "dev"
    return v if v and len(v) <= 40 else "dev"
