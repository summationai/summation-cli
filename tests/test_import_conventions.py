"""Guard against monorepo-absolute imports that break pip editable install."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_BANNED_SUBSTR = "python.packages.sum_cli"


def test_no_monorepo_absolute_imports() -> None:
    offenders: list[str] = []
    for path in _PACKAGE_ROOT.rglob("*.py"):
        if path.name == "test_import_conventions.py":
            continue
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        for line_no, line in enumerate(path.read_text().splitlines(), start=1):
            if _BANNED_SUBSTR in line:
                offenders.append(f"{path.relative_to(_PACKAGE_ROOT)}:{line_no}: {line.strip()}")
    assert not offenders, "Use sum_cli.* paths only (no python.packages.sum_cli):\n" + "\n".join(
        offenders
    )
