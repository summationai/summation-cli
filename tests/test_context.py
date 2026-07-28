"""Context resource and profile_meta tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sum_cli.config_store import META_SECTION, read_all, write_all
from sum_cli.constants import ACTIVE_PROFILE_KEY
from sum_cli.cli.main import app
from sum_cli.profile_meta import parse_profile_account

runner = CliRunner()


def test_parse_profile_account_fanatics_staging() -> None:
    assert parse_profile_account("fanatics_staging") == {
        "tenant": "fanatics",
        "environment": "staging",
    }


def test_parse_profile_account_default() -> None:
    assert parse_profile_account("default") == {
        "tenant": "default",
        "environment": None,
    }


def test_parse_profile_account_onboard2() -> None:
    assert parse_profile_account("onboard2") == {
        "tenant": "onboard2",
        "environment": None,
    }


def _write_profiles(path: Path) -> None:
    write_all(
        path,
        {
            "default": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "id-default",
                "client_secret": "secret-default",
            },
            "fanatics_staging": {
                "base_url": "https://staging-api-fanatics.summation.com",
                "client_id": "id-staging",
                "client_secret": "secret-staging",
            },
        },
    )


def test_config_use_sets_active_profile(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    _write_profiles(cfg_file)
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "use", "fanatics_staging"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["profile"] == "fanatics_staging"
    assert body["result"]["tenant"] == "fanatics"
    assert body["result"]["environment"] == "staging"
    assert body["result"]["base_url"] == "https://staging-api-fanatics.summation.com"

    data = read_all(cfg_file)
    assert data[META_SECTION][ACTIVE_PROFILE_KEY] == "fanatics_staging"


def test_config_use_sets_default_project(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    _write_profiles(cfg_file)
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(
        app,
        ["config", "use", "fanatics_staging", "--project", "prj-test"],
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["result"]["default_project"] == "prj-test"

    data = read_all(cfg_file)
    assert data["fanatics_staging"]["default_project"] == "prj-test"


def test_config_active_includes_tenant_environment(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            META_SECTION: {ACTIVE_PROFILE_KEY: "fanatics_staging"},
            "fanatics_staging": {
                "base_url": "https://staging-api-fanatics.summation.com",
                "client_id": "id",
                "client_secret": "secret",
                "default_project": "prj-abc",
            },
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "active"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["result"]["tenant"] == "fanatics"
    assert body["result"]["environment"] == "staging"
    assert body["result"]["default_project"] == "prj-abc"


def test_config_list_includes_base_url_and_tenant(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            META_SECTION: {ACTIVE_PROFILE_KEY: "fanatics_staging"},
            "fanatics_staging": {
                "base_url": "https://staging-api-fanatics.summation.com",
                "client_id": "id",
                "client_secret": "secret",
            },
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    profile = body["result"]["profiles"][0]
    assert profile["name"] == "fanatics_staging"
    assert profile["active"] is True
    assert profile["base_url"] == "https://staging-api-fanatics.summation.com"
    assert profile["tenant"] == "fanatics"
    assert profile["environment"] == "staging"


def test_config_use_unknown_profile_errors(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    _write_profiles(cfg_file)
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "use", "does_not_exist"])
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "PROFILE_NOT_FOUND"


def test_config_set_project(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    _write_profiles(cfg_file)
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "set-project", "--project", "prj-xyz"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["result"]["default_project"] == "prj-xyz"
