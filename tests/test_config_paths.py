"""Config path resolution tests."""

from __future__ import annotations

from pathlib import Path

from sum_cli.config_paths import resolve_config_path
from sum_cli.config_store import write_all


def test_resolve_config_path_prefers_summation_config(tmp_path: Path, monkeypatch) -> None:
    summation = tmp_path / "summation-config"
    legacy = tmp_path / "config"
    write_all(summation, {"default": {"base_url": "https://a"}})
    write_all(legacy, {"other": {"base_url": "https://b"}})
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(summation))
    assert resolve_config_path() == summation


def test_resolve_config_path_migrates_legacy_file(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    summation_dir = home / ".summation"
    summation_dir.mkdir(parents=True)
    legacy = summation_dir / "config"
    summation = summation_dir / "summation-config"
    write_all(legacy, {"legacy": {"base_url": "https://legacy"}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SUMMATION_CONFIG_FILE", raising=False)
    monkeypatch.setattr("sum_cli.config_paths.DEFAULT_CONFIG_PATH", summation)
    monkeypatch.setattr("sum_cli.config_paths._LEGACY_CONFIG_PATH", legacy)

    assert resolve_config_path() == summation
    assert summation.is_file()
    assert not legacy.exists()
