"""Encode local file content for sum-api FileWriteRequest."""

from __future__ import annotations

import base64
from pathlib import Path

BINARY_EXTENSIONS = frozenset(
    {
        "pdf",
        "docx",
        "doc",
        "xlsx",
        "xls",
        "pptx",
        "ppt",
        "jpg",
        "jpeg",
        "png",
        "gif",
        "svg",
        "bmp",
        "heic",
        "webp",
        "zip",
        "tar",
        "gz",
        "7z",
    }
)


def is_binary_path(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return True
    ext = path.suffix.lstrip(".").lower()
    return ext in BINARY_EXTENSIONS


def read_file_write_payload(path: Path) -> dict[str, str]:
    if is_binary_path(path):
        return {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        }
    return {"content": path.read_text(encoding="utf-8")}
