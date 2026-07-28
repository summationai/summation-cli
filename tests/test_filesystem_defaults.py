"""Filesystem config defaults and set-defaults command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.config_store import read_all
from sum_cli.filesystem.config_defaults import (
    effective_filesystem_defaults,
    read_filesystem_defaults,
    set_filesystem_defaults,
)

runner = CliRunner()


def test_set_filesystem_defaults_writes_toml_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config"
    cfg.write_text('[default]\nbase_url = "https://example.com"\n')
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    set_filesystem_defaults(
        "sharepoint",
        root="drive-1",
        path="folder-1",
    )
    data = read_all(cfg)
    assert data["filesystem"]["sharepoint_root"] == "drive-1"
    assert data["filesystem"]["sharepoint_path"] == "folder-1"


def test_set_defaults_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    result = runner.invoke(
        app,
        [
            "filesystem",
            "set-defaults",
            "--provider",
            "sharepoint",
            "--root",
            "drive-x",
            "--path",
            "folder-y",
        ],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["persisted"] == {"root": "drive-x", "path": "folder-y"}
    stored = read_filesystem_defaults("sharepoint")
    assert stored == {"root": "drive-x", "path": "folder-y"}


def test_effective_defaults_config_beats_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    set_filesystem_defaults("sharepoint", root="from-config", path="path-config")
    monkeypatch.setenv("SHAREPOINT_ROOT", "from-env")
    effective = effective_filesystem_defaults("sharepoint")
    assert effective["root"] == "from-config"
    assert effective["path"] == "path-config"


def test_import_env_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    env = tmp_path / ".env"
    env.write_text(
        "SHAREPOINT_TENANT_ID=tenant-1\n"
        "SHAREPOINT_CLIENT_ID=client-1\n"
        "SHAREPOINT_CLIENT_SECRET=secret-1\n"
        "SHAREPOINT_SITE_URL=host:/sites/Site\n"
        "SHAREPOINT_ROOT='b!drive-id'\n"
        "SHAREPOINT_PATH=folder-1\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["filesystem", "import-env", str(env), "--provider", "sharepoint"],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["defaults"]["root"] == "b!drive-id"
    data = read_all(cfg)
    assert data["sharepoint"]["tenant_id"] == "tenant-1"
    assert data["filesystem"]["sharepoint_root"] == "b!drive-id"


def test_import_env_cli_sectioned_honors_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    env = tmp_path / ".summation-config"
    env.write_text(
        "SUM_API_ACTIVE_PROFILE=fanatics\n"
        "SHAREPOINT_ROOT='b!drive-from-globals'\n"
        "\n"
        "[profile.fanatics]\n"
        "SUM_API_CLIENT_ID=fanatics-id\n"
        "SHAREPOINT_PATH=folder-from-active\n"
        "\n"
        "[profile.other]\n"
        "SHAREPOINT_PATH=folder-from-other\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["filesystem", "import-env", str(env), "--provider", "sharepoint"],
    )
    assert result.exit_code == 0, result.stdout
    data = read_all(cfg)
    assert data["filesystem"]["sharepoint_root"] == "b!drive-from-globals"
    assert data["filesystem"]["sharepoint_path"] == "folder-from-active"
