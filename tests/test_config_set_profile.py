"""config set-profile tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from sum_cli.auth import TokenResult
from sum_cli.cli.main import app
from sum_cli.config_store import read_all

runner = CliRunner()


def test_set_profile_allows_base_url_only_for_device_login(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(
        app,
        ["config", "set-profile", "sandbox", "--base-url", "https://sandbox-api.summation.com"],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["has_m2m_credentials"] is False
    assert body["result"]["login"] is None
    assert body["next_actions"][0]["command"] == "sumcli --profile sandbox auth login"

    stored = read_all(cfg_file)["sandbox"]
    assert stored == {"base_url": "https://sandbox-api.summation.com"}


def test_set_profile_rejects_partial_m2m_credentials(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(
        app,
        [
            "config",
            "set-profile",
            "sandbox",
            "--base-url",
            "https://sandbox-api.summation.com",
            "--client-id",
            "cid",
        ],
    )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "CREDENTIALS_REQUIRED"


def test_set_profile_with_m2m_credentials_can_login(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    with patch(
        "sum_cli.resources.config.login_and_persist",
        side_effect=lambda cfg, http=None: (
            TokenResult("tok", 0.0, expires_at_wall=9999999999.0),
            cfg_file,
        ),
    ):
        result = runner.invoke(
            app,
            [
                "config",
                "set-profile",
                "sandbox",
                "--base-url",
                "https://sandbox-api.summation.com",
                "--client-id",
                "cid",
                "--client-secret",
                "secret",
            ],
        )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["has_m2m_credentials"] is True
    assert body["result"]["login"]["access_token"] == "***"
    assert body["result"]["login"]["token_expires_at"] == 9999999999.0

    stored = read_all(cfg_file)["sandbox"]
    assert stored["base_url"] == "https://sandbox-api.summation.com"
    assert stored["client_id"] == "cid"
    assert stored["client_secret"] == "secret"
