"""auth login and persisted session tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from sum_cli.auth import (
    DeviceLoginStartResult,
    TokenResult,
    acquire_token,
    complete_device_login,
    login_and_persist,
    persisted_token_valid,
    start_device_login,
)
from sum_cli.cli.main import app
from sum_cli.config import load
from sum_cli.config_store import read_all
from sum_cli.constants import TOKEN_EXPIRES_AT_KEY

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def test_login_persists_credentials_and_token(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUM_API_ACCESS_TOKEN", raising=False)

    write_profile = {
        "sandbox": {
            "base_url": "https://sandbox-api.summation.com",
            "client_id": "cid",
            "client_secret": "csecret",
        }
    }
    from sum_cli.config_store import write_all

    write_all(cfg_file, write_profile)

    m2m_body = {
        "access_token": "tok-persisted",
        "expires_in": 3600,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/auth/m2m/token"):
            return httpx.Response(200, json=m2m_body)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    cfg = load(profile="sandbox", config_file=cfg_file)
    result, path = login_and_persist(cfg, httpx.Client(transport=transport))
    assert result.access_token == "tok-persisted"
    assert path == cfg_file

    stored = read_all(cfg_file)["sandbox"]
    assert stored["client_id"] == "cid"
    assert stored["client_secret"] == "csecret"
    assert stored["access_token"] == "tok-persisted"
    assert TOKEN_EXPIRES_AT_KEY in stored
    assert persisted_token_valid(float(stored[TOKEN_EXPIRES_AT_KEY]))


def test_acquire_token_uses_persisted_session_without_network(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUM_API_ACCESS_TOKEN", raising=False)

    expires = int(time.time()) + 3600
    from sum_cli.config_store import write_all

    write_all(
        cfg_file,
        {
            "sandbox": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "cid",
                "client_secret": "csecret",
                "access_token": "cached-tok",
                TOKEN_EXPIRES_AT_KEY: str(expires),
            },
        },
    )

    cfg = load(profile="sandbox", config_file=cfg_file)

    def fail_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(fail_handler)
    token = acquire_token(cfg, httpx.Client(transport=transport), persist=False)
    assert token.access_token == "cached-tok"


def test_acquire_token_prefers_device_login_credential(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    from sum_cli.config_store import write_all

    expires = int(time.time()) + 3600
    write_all(
        cfg_file,
        {
            "sandbox": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "cid",
                "client_secret": "csecret",
                "device_login_credential": "sm_dls_device-session",
                "access_token": "cached-m2m-tok",
                TOKEN_EXPIRES_AT_KEY: str(expires),
            },
        },
    )

    cfg = load(profile="sandbox", config_file=cfg_file)

    def fail_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(fail_handler)
    token = acquire_token(cfg, httpx.Client(transport=transport), persist=False)
    assert token.access_token == "sm_dls_device-session"


def test_device_login_approved_persists_credential_and_clears_m2m_session(
    tmp_path: Path, monkeypatch
) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    from sum_cli.config_store import write_all

    write_all(
        cfg_file,
        {
            "sandbox": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "cid",
                "client_secret": "csecret",
                "access_token": "stale-m2m-token",
                TOKEN_EXPIRES_AT_KEY: str(int(time.time()) + 3600),
            },
        },
    )

    poll_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_calls
        if request.url.path.endswith("/v1/auth/device-logins"):
            return httpx.Response(
                200,
                json={
                    "device_code": "dc-123",
                    "user_code": "SUM-AAAA-BBBB",
                    "verification_uri": "https://app.summation.com/activate",
                    "verification_uri_complete": "https://app.summation.com/activate?code=SUM-AAAA-BBBB",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if request.url.path.endswith("/v1/auth/device-logins/tokens"):
            poll_calls += 1
            if poll_calls == 1:
                return httpx.Response(200, json={"status": "pending"})
            return httpx.Response(
                200,
                json={
                    "status": "approved",
                    "credential": "sm_dls_device-session",
                    "identity": {"email": "user@summation.com", "user_id": "user-1"},
                },
            )
        if request.url.path.endswith("/v1/me"):
            assert request.headers["Authorization"] == "Bearer sm_dls_device-session"
            return httpx.Response(200, json={"identity": {"client_id": "member-1"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    sleep_calls: list[float] = []
    transport = httpx.MockTransport(handler)
    cfg = load(profile="sandbox", config_file=cfg_file)
    with httpx.Client(transport=transport) as http:
        start = start_device_login(cfg, http)
        result = complete_device_login(cfg, start, http, sleep=sleep_calls.append)

    assert start.device_code == "dc-123"
    assert result.status == "approved"
    assert result.config_path == cfg_file
    assert result.identity is not None
    assert result.identity.email == "user@summation.com"
    assert result.verified_identity == {"identity": {"client_id": "member-1"}}
    assert result.verification_error is None
    assert sleep_calls == [5]

    stored = read_all(cfg_file)["sandbox"]
    assert stored["device_login_credential"] == "sm_dls_device-session"
    assert "access_token" not in stored
    assert TOKEN_EXPIRES_AT_KEY not in stored
    assert stored["client_id"] == "cid"
    assert stored["client_secret"] == "csecret"


def test_device_login_denied_does_not_persist_credential(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    from sum_cli.config_store import write_all

    write_all(
        cfg_file,
        {
            "sandbox": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "cid",
                "client_secret": "csecret",
            },
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/auth/device-logins/tokens"):
            return httpx.Response(200, json={"status": "denied"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    cfg = load(profile="sandbox", config_file=cfg_file)
    start = DeviceLoginStartResult(
        device_code="dc-123",
        user_code="SUM-AAAA-BBBB",
        verification_uri="https://app.summation.com/activate",
        verification_uri_complete="https://app.summation.com/activate?code=SUM-AAAA-BBBB",
        expires_in=600,
        interval=5,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = complete_device_login(cfg, start, http, sleep=lambda _seconds: None)

    assert result.status == "denied"
    assert result.config_path is None
    stored = read_all(cfg_file)["sandbox"]
    assert "device_login_credential" not in stored


def test_device_login_pending_until_deadline_returns_expired(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    from sum_cli.config_store import write_all

    write_all(
        cfg_file,
        {
            "sandbox": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "cid",
                "client_secret": "csecret",
            },
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/auth/device-logins/tokens"):
            return httpx.Response(200, json={"status": "pending"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monotonic_values = iter([0.0, 0.0, 1.0, 1.0])
    sleep_calls: list[float] = []
    cfg = load(profile="sandbox", config_file=cfg_file)
    start = DeviceLoginStartResult(
        device_code="dc-123",
        user_code="SUM-AAAA-BBBB",
        verification_uri="https://app.summation.com/activate",
        verification_uri_complete="https://app.summation.com/activate?code=SUM-AAAA-BBBB",
        expires_in=1,
        interval=5,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = complete_device_login(
            cfg,
            start,
            http,
            sleep=sleep_calls.append,
            monotonic=lambda: next(monotonic_values),
        )

    assert result.status == "expired"
    assert result.config_path is None
    assert sleep_calls == [1]
    stored = read_all(cfg_file)["sandbox"]
    assert "device_login_credential" not in stored


def test_auth_login_cli(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUM_API_ACCESS_TOKEN", raising=False)

    from sum_cli.config_store import write_all

    write_all(
        cfg_file,
        {
            "sandbox": {
                "base_url": "https://sandbox-api.summation.com",
                "client_id": "cid",
                "client_secret": "csecret",
            },
        },
    )

    wall = time.time() + 3600
    token_result = TokenResult(
        access_token="cli-tok",
        expires_at=time.monotonic() + 3600,
        expires_at_wall=wall,
    )

    def _fake_login(cfg, http=None):
        from sum_cli.config_store import update_profile_field

        update_profile_field(
            cfg.profile,
            base_url=cfg.base_url,
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            access_token=token_result.access_token,
            token_expires_at=str(int(wall)),
        )
        return token_result, cfg_file

    with patch("sum_cli.resources.auth.login_and_persist", side_effect=_fake_login):
        result = runner.invoke(app, ["--profile", "sandbox", "auth", "login", "--m2m"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["auth_mode"] == "m2m"
    assert read_all(cfg_file)["sandbox"]["access_token"] == "cli-tok"
