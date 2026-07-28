"""config import-env tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from unittest.mock import patch

from sum_cli.auth import TokenResult
from sum_cli.cli.main import app
from sum_cli.config_store import read_all
from sum_cli.env_import import (
    EnvImportError,
    parse_env_file,
    profile_section_from_env,
    required_fields_present,
)

import pytest

runner = CliRunner()


def test_parse_env_file_strips_export_and_whitespace(tmp_path: Path) -> None:
    env = tmp_path / ".summation-config"
    env.write_text(
        "export SUM_API_BASE_URL=https://sandbox-api.summation.com  \n"
        "SUM_API_CLIENT_ID=cid\n"
        "SUM_API_CLIENT_SECRET=csec\n",
        encoding="utf-8",
    )
    raw = parse_env_file(env)
    section = profile_section_from_env(raw)
    assert section["base_url"] == "https://sandbox-api.summation.com"
    assert section["client_id"] == "cid"
    assert section["client_secret"] == "csec"


def test_parse_env_file_honors_active_profile(tmp_path: Path) -> None:
    # RC's repro: active section is not the last one in the file.
    env = tmp_path / ".summation-config"
    env.write_text(
        "SUM_API_ACTIVE_PROFILE=fanatics\n"
        "\n"
        "[profile.fanatics]\n"
        "SUM_API_BASE_URL=https://sandbox-api-fanatics.summation.com\n"
        "SUM_API_CLIENT_ID=fanatics-id\n"
        "SUM_API_CLIENT_SECRET=fanatics-secret\n"
        "\n"
        "[profile.shared]\n"
        "SUM_API_BASE_URL=https://sandbox-api.summation.com\n"
        "SUM_API_CLIENT_ID=shared-id\n"
        "SUM_API_CLIENT_SECRET=shared-secret\n",
        encoding="utf-8",
    )
    section = profile_section_from_env(parse_env_file(env))
    assert section["client_id"] == "fanatics-id"
    assert section["base_url"] == "https://sandbox-api-fanatics.summation.com"


def test_parse_env_file_sectioned_without_active_marker(tmp_path: Path) -> None:
    env = tmp_path / ".summation-config"
    env.write_text(
        "[profile.a]\nSUM_API_CLIENT_ID=a\n[profile.b]\nSUM_API_CLIENT_ID=b\n",
        encoding="utf-8",
    )
    with pytest.raises(EnvImportError) as exc:
        parse_env_file(env)
    assert exc.value.code == "ACTIVE_PROFILE_REQUIRED"


def test_parse_env_file_active_profile_missing_section(tmp_path: Path) -> None:
    env = tmp_path / ".summation-config"
    env.write_text(
        "SUM_API_ACTIVE_PROFILE=ghost\n[profile.a]\nSUM_API_CLIENT_ID=a\n",
        encoding="utf-8",
    )
    with pytest.raises(EnvImportError) as exc:
        parse_env_file(env)
    assert exc.value.code == "ACTIVE_PROFILE_NOT_FOUND"


def test_parse_env_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_env_file(tmp_path / "absent")


def test_required_fields_present_reports_missing() -> None:
    assert required_fields_present({"client_id": "id", "client_secret": "sec"}) == []
    assert required_fields_present({"client_id": "id"}) == ["SUM_API_CLIENT_SECRET"]
    assert required_fields_present({}) == ["SUM_API_CLIENT_ID", "SUM_API_CLIENT_SECRET"]


def test_import_env_cli(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    env = tmp_path / "skill.env"
    env.write_text(
        "SUM_API_BASE_URL=https://example.com\n"
        "SUM_API_CLIENT_ID=id1\n"
        "SUM_API_CLIENT_SECRET=secret1\n",
        encoding="utf-8",
    )

    with patch(
        "sum_cli.resources.config.login_and_persist",
        side_effect=lambda cfg, http=None: (
            TokenResult("tok", 0.0, expires_at_wall=9999999999.0),
            cfg_file,
        ),
    ):
        result = runner.invoke(
            app,
            ["config", "import-env", str(env), "--profile", "imported", "--no-login"],
        )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    stored = read_all(cfg_file)["imported"]
    assert stored["base_url"] == "https://example.com"
    assert stored["client_id"] == "id1"
    assert stored["client_secret"] == "secret1"
