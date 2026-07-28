"""Temp file helpers."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path


def write_stream(*, dest: Path, chunks: Iterator[bytes]) -> int:
    """Write streamed chunks to ``dest``; return total bytes written."""
    total = 0
    with dest.open("wb") as out:
        for chunk in chunks:
            out.write(chunk)
            total += len(chunk)
    return total


def write_temp_stream(
    *, prefix: str, suffix: str = "", chunks: Iterator[bytes]
) -> tuple[Path, int]:
    """Stream chunks to a new temp file; return path and total bytes written."""
    fd, path_str = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    path = Path(path_str)
    return path, write_stream(dest=path, chunks=chunks)


def write_temp_bytes(*, prefix: str, suffix: str = "", data: bytes) -> Path:
    """Write bytes to a new temp file; close the mkstemp fd immediately."""
    fd, path_str = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    path = Path(path_str)
    path.write_bytes(data)
    return path
