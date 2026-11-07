"""Atomic local configuration writes. Runtime files never belong in a release."""
import json
import os
from pathlib import Path
import tempfile


def atomic_write(path: Path, content: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value, mode=0o600):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", mode)
