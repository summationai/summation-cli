"""debug_log helpers."""

from __future__ import annotations

import base64
import json

from sum_cli import debug_log


def test_jwt_claims_summary_extracts_sub() -> None:
    payload = {"sub": "m2m-client-live-test", "scope": "agent:read", "iss": "stytch.com/project"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{encoded}.sig"
    summary = debug_log.jwt_claims_summary(token)
    assert summary["sub"] == "m2m-client-live-test"
    assert summary["scope"] == "agent:read"


def test_verbose_env_enables_logging(monkeypatch) -> None:
    debug_log.set_verbose(False)
    monkeypatch.setenv("SUMCLI_VERBOSE", "1")
    assert debug_log.verbose_enabled() is True
