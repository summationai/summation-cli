"""Unit tests for filesystem.import_env.import_sharepoint_from_env_file."""

from __future__ import annotations

from pathlib import Path

import pytest

from sum_cli.config_store import read_all
from sum_cli.filesystem.config_defaults import FILESYSTEM_SECTION, read_filesystem_defaults
from sum_cli.filesystem.import_env import import_sharepoint_from_env_file
from sum_cli.filesystem.sharepoint import SHAREPOINT_SECTION

_FULL_CREDS = (
    "SHAREPOINT_TENANT_ID=tenant-1\n"
    "SHAREPOINT_CLIENT_ID=client-1\n"
    "SHAREPOINT_CLIENT_SECRET=super-secret\n"
    "SHAREPOINT_SITE_URL=host:/sites/Site\n"
)


def _use_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    return cfg


def test_full_credentials_and_defaults_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _use_config(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text(
        _FULL_CREDS + 'SHAREPOINT_ROOT="b!drive-1"\nSHAREPOINT_PATH=folder-1\n',
        encoding="utf-8",
    )

    result = import_sharepoint_from_env_file(env)

    assert result.config_path == cfg
    assert result.imported_from == env
    assert result.defaults == {"root": "b!drive-1", "path": "folder-1"}

    stored = read_all(cfg)
    assert stored[SHAREPOINT_SECTION]["tenant_id"] == "tenant-1"
    assert stored[SHAREPOINT_SECTION]["site_url"] == "host:/sites/Site"
    assert read_filesystem_defaults("sharepoint") == {"root": "b!drive-1", "path": "folder-1"}


def test_returned_credentials_redact_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_config(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text(_FULL_CREDS, encoding="utf-8")

    result = import_sharepoint_from_env_file(env)

    assert result.credentials["tenant_id"] == "tenant-1"
    assert result.credentials["client_secret"] != "super-secret"
    assert "super-secret" not in str(result.credentials)
    assert result.defaults == {}


def test_defaults_only_without_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _use_config(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("SHAREPOINT_ROOT=b!only-root\n", encoding="utf-8")

    result = import_sharepoint_from_env_file(env)

    assert result.config_path == cfg
    assert result.defaults == {"root": "b!only-root"}
    assert all(v is None for v in result.credentials.values())
    stored = read_all(cfg)
    assert SHAREPOINT_SECTION not in stored
    assert stored[FILESYSTEM_SECTION]["sharepoint_root"] == "b!only-root"


def test_no_sharepoint_keys_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("SUM_API_CLIENT_ID=irrelevant\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No SharePoint keys found"):
        import_sharepoint_from_env_file(env)


def test_partial_credentials_raise_with_missing_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_config(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text(
        "SHAREPOINT_TENANT_ID=tenant-1\nSHAREPOINT_CLIENT_ID=client-1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc:
        import_sharepoint_from_env_file(env)
    assert "Incomplete SharePoint credentials" in str(exc.value)
    assert "SHAREPOINT_CLIENT_SECRET" in str(exc.value)
    assert "SHAREPOINT_SITE_URL" in str(exc.value)


def test_expanduser_on_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError):
        import_sharepoint_from_env_file(tmp_path / "does-not-exist.env")
