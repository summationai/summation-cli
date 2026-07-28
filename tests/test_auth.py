"""auth status and whoami command tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.auth import DeviceLoginCompleteResult, DeviceLoginLogoutResult, DeviceLoginStartResult
from sum_cli.config_store import write_all

runner = CliRunner()


def _load_last_json(output: str) -> dict:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("Expected command output")
    return json.loads(lines[-1])


def _mock_client(body: object) -> MagicMock:
    mock_client = MagicMock()
    mock_client.request.return_value = body
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_cm


def _write_config(tmp_path: Path, **profile_fields: str) -> Path:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://example.com",
                **profile_fields,
            }
        },
    )
    return cfg_file


def _mock_http_client() -> MagicMock:
    http = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = http
    cm.__exit__.return_value = None
    return cm


def test_auth_login_defaults_to_device_login(tmp_path: Path, monkeypatch) -> None:
    cfg_file = _write_config(tmp_path)
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    start = DeviceLoginStartResult(
        device_code="dc-123",
        user_code="SUM-AAAA-BBBB",
        verification_uri="https://app.summation.com/activate",
        verification_uri_complete="https://app.summation.com/activate?code=SUM-AAAA-BBBB",
        expires_in=600,
        interval=5,
    )
    complete = DeviceLoginCompleteResult(
        status="approved",
        profile="default",
        config_path=cfg_file,
        verified_identity={"identity": {"client_id": "member-1"}},
    )

    with (
        patch("sum_cli.resources.auth.httpx.Client", return_value=_mock_http_client()),
        patch("sum_cli.resources.auth.start_device_login", return_value=start),
        patch("sum_cli.resources.auth.complete_device_login", return_value=complete),
    ):
        result = runner.invoke(app, ["auth", "login"])

    assert result.exit_code == 0
    body = _load_last_json(result.stdout)
    assert body["ok"] is True
    assert body["result"]["auth_mode"] == "device_login"
    assert body["result"]["profile"] == "default"


def test_auth_logout_revokes_device_login(tmp_path: Path, monkeypatch) -> None:
    cfg_file = _write_config(tmp_path, device_login_credential="sm_dls_tok")
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    logout = DeviceLoginLogoutResult(success=True, profile="default", config_path=cfg_file)
    with (
        patch("sum_cli.resources.auth.httpx.Client", return_value=_mock_http_client()),
        patch("sum_cli.resources.auth.revoke_device_login_session", return_value=logout),
    ):
        result = runner.invoke(app, ["auth", "logout"])

    assert result.exit_code == 0
    body = _load_last_json(result.stdout)
    assert body["ok"] is True
    assert body["result"]["revoked"] is True
    assert body["result"]["auth_mode"] == "device_login"


def test_auth_whoami_reports_device_login_mode(tmp_path: Path, monkeypatch) -> None:
    cfg_file = _write_config(tmp_path, device_login_credential="sm_dls_tok")
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    with patch(
        "sum_cli.resources.auth.api_client",
        return_value=_mock_client({"data": {"identity": {"email": "user@example.com"}}}),
    ):
        result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0
    body = _load_last_json(result.stdout)
    assert body["ok"] is True
    assert body["result"]["auth_mode"] == "device_login"
    assert body["result"]["session_persisted"] is True


def test_auth_status_emits_status_not_whoami(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    status_body = {"data": {"authenticated": True, "method": "m2m"}}
    with patch(
        "sum_cli.resources.auth.api_client",
        return_value=_mock_client(status_body),
    ):
        result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    body = _load_last_json(result.stdout)
    assert body["ok"] is True
    assert body["result"]["status"]["authenticated"] is True
    assert "identity" not in body["result"]


def test_auth_status_missing_authenticated_defaults_false(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    status_body = {"data": {"method": "m2m"}}
    with patch(
        "sum_cli.resources.auth.api_client",
        return_value=_mock_client(status_body),
    ):
        result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    body = _load_last_json(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_AUTHENTICATED"


def test_auth_status_false(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    status_body = {"data": {"authenticated": False}}
    with patch(
        "sum_cli.resources.auth.api_client",
        return_value=_mock_client(status_body),
    ):
        result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    body = _load_last_json(result.stdout)
    assert body["error"]["code"] == "NOT_AUTHENTICATED"
