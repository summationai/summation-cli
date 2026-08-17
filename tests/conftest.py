"""Shared pytest isolation fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_summation_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from reading or mutating the user's real shared config."""
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(tmp_path / "summation-config"))
    # Version checks hit PyPI; keep the suite offline unless a test opts in.
    monkeypatch.setenv("SUMCLI_NO_UPDATE_CHECK", "1")
    # Agent (non-TTY) commands require --intent; tests opt in via the env var
    # unless they are exercising the missing-intent path.
    monkeypatch.setenv("SUMCLI_INTENT", "test")
