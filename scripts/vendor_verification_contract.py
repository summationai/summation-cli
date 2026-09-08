#!/usr/bin/env python3
"""Vendor the canonical Code custom-verification contract into summation-cli."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "sum_cli" / "verification_contract"
SOURCE_DIRECTORY = Path("python/libs/sm_verification_contract")
SOURCES = ("rubric_types.py", "custom_test_bundle.py")
CANONICAL_IMPORT = "from python.libs.sm_verification_contract.rubric_types import RubricTest"
VENDORED_IMPORT = "from sum_cli.verification_contract.rubric_types import RubricTest"


def _source_commit(code_root: Path) -> str:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(code_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(code_root),
                "diff",
                "--quiet",
                "HEAD",
                "--",
                *(str(SOURCE_DIRECTORY / filename) for filename in SOURCES),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return commit
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f"cannot vendor from {code_root}: resolve HEAD and commit any contract "
            f"changes first ({exc})"
        ) from exc


def _render(code_root: Path, filename: str, commit: str) -> str:
    source_path = code_root / SOURCE_DIRECTORY / filename
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read canonical contract file {source_path}: {exc}") from exc
    if filename == "custom_test_bundle.py":
        if CANONICAL_IMPORT not in source:
            raise ValueError(
                f"canonical import changed in {source_path}; update the vendor transform"
            )
        source = source.replace(CANONICAL_IMPORT, VENDORED_IMPORT, 1)
    relative_source = (SOURCE_DIRECTORY / filename).as_posix()
    header = (
        "# GENERATED FILE - DO NOT EDIT.\n"
        "# ruff: noqa: E501\n"
        f"# Source: summationai/Code@{commit}:{relative_source}\n"
        "# Regenerate: python scripts/vendor_verification_contract.py --code-root PATH\n\n"
    )
    return header + source


def _run(code_root: Path, *, destination: Path, check: bool) -> int:
    code_root = code_root.resolve()
    commit = _source_commit(code_root)
    expected = {filename: _render(code_root, filename, commit) for filename in SOURCES}
    if check:
        stale: list[str] = []
        for filename, content in expected.items():
            destination_file = destination / filename
            try:
                actual = destination_file.read_text(encoding="utf-8")
            except OSError:
                actual = ""
            if actual != content:
                stale.append(filename)
        if stale:
            print(
                "Vendored verification contract is stale: " + ", ".join(stale),
                file=sys.stderr,
            )
            print(
                "Regenerate with: python "
                f"{Path(__file__).relative_to(ROOT)} --code-root {code_root}",
                file=sys.stderr,
            )
            return 1
        print(f"Vendored verification contract matches Code@{commit}.")
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    for filename, content in expected.items():
        destination_file = destination / filename
        destination_file.write_text(content, encoding="utf-8")
        try:
            display_path = destination_file.relative_to(ROOT)
        except ValueError:
            display_path = destination_file
        print(f"Updated {display_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--code-root",
        required=True,
        type=Path,
        help="Path to a summationai/Code checkout containing the canonical contract.",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=DESTINATION,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--check", action="store_true", help="Fail if committed vendored files differ."
    )
    args = parser.parse_args()
    try:
        return _run(args.code_root, destination=args.destination.resolve(), check=args.check)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
