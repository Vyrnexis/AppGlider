"""Atomic writes for installation records and disposable release metadata."""

import json
import os
import tempfile
from pathlib import Path


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    stage = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def atomic_json(path: Path, data: object) -> None:
    atomic_bytes(path, json.dumps(data, indent=2).encode("utf-8"))
