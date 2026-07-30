"""Multi-profile config resolution tests."""

from __future__ import annotations

from pathlib import Path

from sum_cli.config import load
from sum_cli.config_store import (
    DEFAULT_CONFIG_PATH,
    config_path,
    read_all,
    set_active_profile,
    write_all,
)
from sum_cli.constants import ACTIVE_PROFILE_KEY, META_SECTION


def test_config_path_uses_shared_default_and_honors_override(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("SUMMATION_CONFIG_FILE", raising=False)
    assert DEFAULT_CONFIG_PATH == Path.home() / ".summation" / "summation-config"
    assert config_path() == DEFAULT_CONFIG_PATH

    override = tmp_path / "custom-config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(override))
    assert config_path() == override


def test_load_profile_sections(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            META_SECTION: {ACTIVE_PROFILE_KEY: "fanatics"},
            "default": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "id-default",
                "client_secret": "secret-default",
            },
            "fanatics": {
                "base_url": "https://sandbox-api-fanatics.summation.com",
                "client_id": "id-fanatics",
                "client_secret": "secret-fanatics",
                "default_project": "proj_fan",
            },
        },
    )
    monkeypatch.delenv("SUMMATION_PROFILE", raising=False)
    monkeypatch.delenv("SUM_API_CLIENT_ID", raising=False)

    default_cfg = load(config_file=cfg_file, profile="default")
    assert default_cfg.client_id == "id-default"
    assert "summation.com" in default_cfg.base_url
    assert default_cfg.default_project is None

    fanatics_cfg = load(config_file=cfg_file, profile="fanatics")
    assert fanatics_cfg.client_id == "id-fanatics"
    assert "fanatics" in fanatics_cfg.base_url
    assert fanatics_cfg.default_project == "proj_fan"

    active_cfg = load(config_file=cfg_file)
    assert active_cfg.profile == "fanatics"
    assert active_cfg.client_id == "id-fanatics"


def test_set_active_profile_persists_meta(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {"base_url": "https://a", "client_id": "a", "client_secret": "b"},
            "other": {"base_url": "https://b", "client_id": "c", "client_secret": "d"},
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    set_active_profile("other")
    data = read_all(cfg_file)
    assert data[META_SECTION][ACTIVE_PROFILE_KEY] == "other"
