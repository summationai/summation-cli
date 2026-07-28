"""Project resolution precedence tests."""

from __future__ import annotations

from pathlib import Path

from sum_cli.config import load
from sum_cli.config_store import write_all
from sum_cli.constants import ACTIVE_PROFILE_KEY, META_SECTION
from sum_cli.project_context import resolve_project


def test_explicit_project_wins() -> None:
    assert resolve_project(explicit="proj_explicit") == "proj_explicit"


def test_default_project_from_profile_toml(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            META_SECTION: {ACTIVE_PROFILE_KEY: "default"},
            "default": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "id",
                "client_secret": "secret",
                "default_project": "proj_from_file",
            },
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUMMATION_PROJECT", raising=False)
    cfg = load(config_file=cfg_file)
    assert resolve_project(cfg) == "proj_from_file"


def test_summation_project_env_when_no_file_default(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "id",
                "client_secret": "secret",
            },
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_from_env")
    cfg = load(config_file=cfg_file)
    assert resolve_project(cfg) == "proj_from_env"


def test_precedence_explicit_over_file_over_env(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "id",
                "client_secret": "secret",
                "default_project": "proj_from_file",
            },
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_from_env")
    cfg = load(config_file=cfg_file)

    assert resolve_project(cfg, explicit="proj_explicit") == "proj_explicit"
    assert resolve_project(cfg) == "proj_from_file"
