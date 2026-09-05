from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from sum_cli.verification_contract.custom_test_bundle import CUSTOM_TEST_BUNDLE_VERSION

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = Path("python/libs/sm_verification_contract")
VENDORED_DIRECTORY = ROOT / "sum_cli/verification_contract"
CANONICAL_IMPORT = "from python.libs.sm_verification_contract.rubric_types import RubricTest"
VENDORED_IMPORT = "from sum_cli.verification_contract.rubric_types import RubricTest"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def test_vendor_script_generates_and_checks_exact_contract(tmp_path: Path) -> None:
    code_root = tmp_path / "Code"
    source_directory = code_root / SOURCE_DIRECTORY
    source_directory.mkdir(parents=True)
    for filename in ("custom_test_bundle.py", "rubric_types.py"):
        vendored = (VENDORED_DIRECTORY / filename).read_text(encoding="utf-8")
        canonical = "\n".join(vendored.splitlines()[5:]) + "\n"
        canonical = canonical.replace(VENDORED_IMPORT, CANONICAL_IMPORT, 1)
        (source_directory / filename).write_text(canonical, encoding="utf-8")
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "test@example.test"),
        ("git", "config", "user.name", "Contract Test"),
        ("git", "add", "."),
        ("git", "commit", "-qm", "canonical contract"),
    ):
        result = _run(*command, cwd=code_root)
        assert result.returncode == 0, result.stdout + result.stderr
    destination = tmp_path / "vendored"
    script = ROOT / "scripts/vendor_verification_contract.py"

    generate = _run(
        sys.executable,
        str(script),
        "--code-root",
        str(code_root),
        "--destination",
        str(destination),
        cwd=ROOT,
    )
    check = _run(
        sys.executable,
        str(script),
        "--code-root",
        str(code_root),
        "--destination",
        str(destination),
        "--check",
        cwd=ROOT,
    )

    assert generate.returncode == 0, generate.stdout + generate.stderr
    assert check.returncode == 0, check.stdout + check.stderr
    source_commit = _run("git", "rev-parse", "HEAD", cwd=code_root).stdout.strip()
    assert source_commit in (destination / "custom_test_bundle.py").read_text()


def test_vendored_contract_records_source_commit_and_version() -> None:
    source_commits: set[str] = set()
    for filename in ("custom_test_bundle.py", "rubric_types.py"):
        generated = (VENDORED_DIRECTORY / filename).read_text(encoding="utf-8")
        match = re.search(r"summationai/Code@([0-9a-f]{40}):", generated)
        assert match is not None
        source_commits.add(match.group(1))
        assert "GENERATED FILE - DO NOT EDIT" in generated
    assert len(source_commits) == 1
    assert CUSTOM_TEST_BUNDLE_VERSION == "custom-test-bundle/v1"


def test_vendor_check_refuses_dirty_canonical_sources(tmp_path: Path) -> None:
    code_root = tmp_path / "Code"
    source_directory = code_root / SOURCE_DIRECTORY
    source_directory.mkdir(parents=True)
    for filename in ("custom_test_bundle.py", "rubric_types.py"):
        canonical = (
            "\n".join((VENDORED_DIRECTORY / filename).read_text(encoding="utf-8").splitlines()[5:])
            + "\n"
        )
        (source_directory / filename).write_text(
            canonical.replace(VENDORED_IMPORT, CANONICAL_IMPORT, 1), encoding="utf-8"
        )
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "test@example.test"),
        ("git", "config", "user.name", "Contract Test"),
        ("git", "add", "."),
        ("git", "commit", "-qm", "canonical contract"),
    ):
        assert _run(*command, cwd=code_root).returncode == 0
    (source_directory / "rubric_types.py").write_text("# dirty\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/vendor_verification_contract.py"),
            "--code-root",
            str(code_root),
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "commit any contract changes first" in result.stderr
