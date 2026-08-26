"""Config path resolution tests."""

from __future__ import annotations

from pathlib import Path

from sum_cli.config import load
from sum_cli.config_paths import resolve_config_path
from sum_cli.config_store import write_all


def test_resolve_config_path_honors_summation_config_file_env(
    tmp_path: Path, monkeypatch
) -> None:
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


def test_config_load_migrates_legacy_without_update_check(tmp_path: Path, monkeypatch) -> None:
    """config.load() must migrate legacy config even when SUMCLI_NO_UPDATE_CHECK=1."""
    home = tmp_path / "home"
    summation_dir = home / ".summation"
    summation_dir.mkdir(parents=True)
    legacy = summation_dir / "config"
    summation = summation_dir / "summation-config"
    write_all(
        legacy,
        {
            "default": {
                "base_url": "https://legacy.example.com",
                "access_token": "legacytoken",
            }
        },
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SUMMATION_CONFIG_FILE", raising=False)
    monkeypatch.setenv("SUMCLI_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr("sum_cli.config_paths.DEFAULT_CONFIG_PATH", summation)
    monkeypatch.setattr("sum_cli.config_paths._LEGACY_CONFIG_PATH", legacy)

    cfg = load()

    assert cfg.base_url == "https://legacy.example.com"
    assert cfg.access_token == "legacytoken"
    assert summation.is_file()
    assert not legacy.exists()


def test_resolve_config_path_falls_back_when_migration_fails(tmp_path: Path, monkeypatch) -> None:
    """An unwritable ~/.summation must not brick every command: read the legacy file."""
    home = tmp_path / "home"
    summation_dir = home / ".summation"
    summation_dir.mkdir(parents=True)
    legacy = summation_dir / "config"
    summation = summation_dir / "summation-config"
    write_all(legacy, {"default": {"base_url": "https://legacy.example.com"}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SUMMATION_CONFIG_FILE", raising=False)
    monkeypatch.setattr("sum_cli.config_paths.DEFAULT_CONFIG_PATH", summation)
    monkeypatch.setattr("sum_cli.config_paths._LEGACY_CONFIG_PATH", legacy)
    summation_dir.chmod(0o500)
    try:
        assert resolve_config_path() == legacy
        assert load().base_url == "https://legacy.example.com"
    finally:
        summation_dir.chmod(0o700)
