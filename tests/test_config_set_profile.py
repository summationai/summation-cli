"""config set-profile tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from sum_cli.auth import TokenResult, acquire_token
from sum_cli.cli.main import app
from sum_cli.config import load
from sum_cli.config_store import read_all, write_all

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


def _seed(cfg_file: Path, section: dict[str, str]) -> None:
    write_all(cfg_file, {"work": section})


def test_set_profile_keeps_device_login_credential_on_an_existing_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """AX-054: set-profile must not sign the machine out of a profile it is updating."""
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    _seed(
        cfg_file,
        {
            "base_url": "https://sandbox-api.summation.com",
            "device_login_credential": "seeded-credential",
            "default_project": "prj-seeded",
        },
    )

    result = runner.invoke(
        app,
        ["config", "set-profile", "work", "--base-url", "https://api.summation.com"],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["merged"] is True
    assert body["result"]["created"] is False
    assert body["result"]["kept"] == ["default_project", "device_login_credential"]
    assert body["result"]["changed"] == ["base_url"]
    assert body["result"]["discarded"] == []
    assert body["result"]["auth_will_use"] == "device_login_credential"

    stored = read_all(cfg_file)["work"]
    assert stored["device_login_credential"] == "seeded-credential"
    assert stored["base_url"] == "https://api.summation.com"
    assert stored["default_project"] == "prj-seeded"

    # No secret value is echoed: kept/changed carry key names only.
    assert "seeded-credential" not in result.stdout


def test_set_profile_replace_discards_the_credential_and_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    _seed(
        cfg_file,
        {
            "base_url": "https://sandbox-api.summation.com",
            "device_login_credential": "seeded-credential",
        },
    )

    result = runner.invoke(
        app,
        [
            "config",
            "set-profile",
            "work",
            "--base-url",
            "https://api.summation.com",
            "--replace",
        ],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["result"]["merged"] is False
    assert body["result"]["kept"] == []
    assert body["result"]["discarded"] == ["device_login_credential"]
    assert body["result"]["auth_will_use"] is None
    assert body["next_actions"][0]["command"] == "sumcli --profile work auth login"

    stored = read_all(cfg_file)["work"]
    assert stored == {"base_url": "https://api.summation.com"}


def test_set_profile_keeps_m2m_credentials_on_an_existing_profile(
    tmp_path: Path, monkeypatch
) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    _seed(
        cfg_file,
        {
            "base_url": "https://sandbox-api.summation.com",
            "client_id": "cid",
            "client_secret": "secret",
            "m2m_scope": "read",
        },
    )

    result = runner.invoke(
        app,
        ["config", "set-profile", "work", "--base-url", "https://api.summation.com"],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["result"]["kept"] == ["client_id", "client_secret", "m2m_scope"]
    assert body["result"]["auth_will_use"] == "m2m_client_credentials"

    stored = read_all(cfg_file)["work"]
    assert stored["client_id"] == "cid"
    assert stored["client_secret"] == "secret"
    assert stored["m2m_scope"] == "read"
    assert stored["base_url"] == "https://api.summation.com"


def test_set_profile_keeps_both_credential_kinds_and_names_the_one_auth_uses(
    tmp_path: Path, monkeypatch
) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    _seed(
        cfg_file,
        {
            "base_url": "https://api.summation.com",
            "device_login_credential": "seeded-credential",
        },
    )

    result = runner.invoke(
        app,
        [
            "config",
            "set-profile",
            "work",
            "--base-url",
            "https://api.summation.com",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--no-login",
        ],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["result"]["kept"] == ["device_login_credential"]
    assert sorted(body["result"]["changed"]) == ["client_id", "client_secret"]
    # Both kinds are stored; acquire_token prefers the device login, and the payload says so.
    assert body["result"]["auth_will_use"] == "device_login_credential"

    stored = read_all(cfg_file)["work"]
    assert stored["device_login_credential"] == "seeded-credential"
    assert stored["client_id"] == "cid"


def test_whoami_still_authenticates_after_set_profile_merged_the_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """The end the person cares about: the machine is still signed in."""
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    _seed(
        cfg_file,
        {
            "base_url": "https://sandbox-api.summation.com",
            "device_login_credential": "seeded-credential",
        },
    )

    set_result = runner.invoke(
        app,
        ["config", "set-profile", "work", "--base-url", "https://api.summation.com"],
    )
    assert set_result.exit_code == 0

    cfg = load(profile="work", config_file=cfg_file)
    with httpx.Client() as http:
        token = acquire_token(cfg, http)
    assert token.access_token == "seeded-credential"
