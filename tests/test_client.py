"""M2M token cache and expiry tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from sum_cli.auth import (
    TokenResult,
    exchange_m2m_token,
    m2m_token_expires_at_monotonic,
    token_cache_valid,
)
from sum_cli.client import Client
from sum_cli.config import Config

FIXTURES = Path(__file__).parent / "fixtures"
M2M_FIXTURE = json.loads((FIXTURES / "m2m_token_response.json").read_text())


def test_m2m_fixture_has_expires_in() -> None:
    assert M2M_FIXTURE["expires_in"] == 3600
    assert "access_token" in M2M_FIXTURE


def test_m2m_token_expires_at_from_expires_in() -> None:
    before = time.monotonic()
    expires = m2m_token_expires_at_monotonic(M2M_FIXTURE, M2M_FIXTURE["access_token"])
    assert expires >= before + 3600 - 1


def test_token_cache_valid_respects_skew() -> None:
    soon = time.monotonic() + 30
    assert not token_cache_valid(soon)
    later = time.monotonic() + 120
    assert token_cache_valid(later)


def test_client_reuses_cached_token(monkeypatch) -> None:
    Client.clear_token_cache()
    cfg = Config(
        base_url="https://example.com",
        access_token=None,
        device_login_credential=None,
        client_id="cid",
        client_secret="secret",
        m2m_scope=None,
        profile="default",
        default_project=None,
        source="test",
    )
    call_count = 0

    def fake_post(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = M2M_FIXTURE
        resp.text = ""
        return resp

    http = MagicMock()
    http.post.side_effect = fake_post

    far_future = time.monotonic() + 10_000
    monkeypatch.setattr(
        "sum_cli.client.acquire_token",
        lambda _cfg, _http: TokenResult(M2M_FIXTURE["access_token"], far_future),
    )

    with Client(cfg) as client:
        t1 = client.token()
        t2 = client.token()
    assert t1 == t2
    assert call_count == 0

    Client.clear_token_cache()


def test_client_refetches_when_m2m_client_secret_changes(monkeypatch) -> None:
    """Rotated client_secret must not reuse a cached token keyed only by client_id."""
    Client.clear_token_cache()
    far_future = time.monotonic() + 10_000
    base = dict(
        base_url="https://example.com",
        access_token=None,
        device_login_credential=None,
        client_id="cid",
        m2m_scope=None,
        profile="default",
        default_project=None,
        source="test",
    )

    def fake_acquire(cfg: Config, _http: object) -> TokenResult:
        secret = cfg.client_secret or ""
        return TokenResult(f"token-{secret}", far_future)

    monkeypatch.setattr("sum_cli.client.acquire_token", fake_acquire)

    with Client(Config(client_secret="secret-a", **base)) as c1:
        assert c1.token() == "token-secret-a"
    with Client(Config(client_secret="secret-b", **base)) as c2:
        assert c2.token() == "token-secret-b"

    Client.clear_token_cache()


def test_client_refetches_when_m2m_scope_changes(monkeypatch) -> None:
    Client.clear_token_cache()
    far_future = time.monotonic() + 10_000
    base = dict(
        base_url="https://example.com",
        access_token=None,
        device_login_credential=None,
        client_id="cid",
        client_secret="secret",
        profile="default",
        default_project=None,
        source="test",
    )

    def fake_acquire(cfg: Config, _http: object) -> TokenResult:
        scope = cfg.m2m_scope or ""
        return TokenResult(f"token-{scope}", far_future)

    monkeypatch.setattr("sum_cli.client.acquire_token", fake_acquire)

    with Client(Config(m2m_scope="scope-a", **base)) as c1:
        assert c1.token() == "token-scope-a"
    with Client(Config(m2m_scope="scope-b", **base)) as c2:
        assert c2.token() == "token-scope-b"

    Client.clear_token_cache()


def test_exchange_m2m_token_parses_fixture() -> None:
    http = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = M2M_FIXTURE
    http.post.return_value = resp
    cfg = Config(
        base_url="https://example.com",
        access_token=None,
        device_login_credential=None,
        client_id="cid",
        client_secret="secret",
        m2m_scope=None,
        profile="default",
        default_project=None,
        source="test",
    )
    result = exchange_m2m_token(cfg, http)
    assert result.access_token == M2M_FIXTURE["access_token"]
    assert token_cache_valid(result.expires_at)


def test_client_refetches_when_device_login_credential_changes(monkeypatch) -> None:
    Client.clear_token_cache()
    far_future = time.monotonic() + 10_000
    base = dict(
        base_url="https://example.com",
        access_token=None,
        client_id="cid",
        client_secret="secret",
        m2m_scope=None,
        profile="default",
        default_project=None,
        source="test",
    )

    def fake_acquire(cfg: Config, _http: object) -> TokenResult:
        credential = cfg.device_login_credential or ""
        return TokenResult(credential, far_future)

    monkeypatch.setattr("sum_cli.client.acquire_token", fake_acquire)

    with Client(Config(device_login_credential="sm_dls_a", **base)) as c1:
        assert c1.token() == "sm_dls_a"
    with Client(Config(device_login_credential="sm_dls_b", **base)) as c2:
        assert c2.token() == "sm_dls_b"

    Client.clear_token_cache()
