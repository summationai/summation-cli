"""Debug logging for auth and HTTP troubleshooting (stderr only, secrets redacted)."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from typing import Any

from sum_cli.config import Config
from sum_cli.config_store import redact
from sum_cli.constants import TOKEN_CACHE_SKEW_SECONDS

logger = logging.getLogger(__name__)

_VERBOSE = False
_CONFIGURED = False


def verbose_enabled() -> bool:
    if _VERBOSE:
        return True
    return os.environ.get("SUMCLI_VERBOSE", "").strip().lower() in {"1", "true", "yes"}


def set_verbose(enabled: bool) -> None:
    global _VERBOSE, _CONFIGURED
    _VERBOSE = enabled
    _configure_logging()


def _configure_logging() -> None:
    global _CONFIGURED
    level = logging.DEBUG if verbose_enabled() else logging.WARNING
    root = logging.getLogger("sum_cli")
    root.setLevel(level)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("sumcli %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        _CONFIGURED = True


def _debug(msg: str, *args: object) -> None:
    if verbose_enabled():
        logger.debug(msg, *args)


def debug(msg: str, *args: object) -> None:
    """Emit a debug line when ``--verbose`` or ``SUMCLI_VERBOSE`` is set."""
    _debug(msg, *args)


def token_source_label(cfg: Config) -> str:
    if (
        cfg.file_access_token
        and cfg.token_expires_at is not None
        and time.time() < cfg.token_expires_at - TOKEN_CACHE_SKEW_SECONDS
    ):
        return f"{cfg.source}#access_token (persisted)"
    if cfg.has_m2m:
        return "m2m:POST /v1/auth/m2m/token"
    return "none"


def jwt_claims_summary(access_token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (debug only)."""
    parts = access_token.split(".")
    if len(parts) != 3:
        return {"error": "not a JWT"}
    try:
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": str(exc)}
    if not isinstance(claims, dict):
        return {"error": "payload not an object"}
    reserved = {"aud", "exp", "iat", "iss", "jti", "nbf", "scope", "sub"}
    custom = {k: v for k, v in claims.items() if k not in reserved}
    return {
        "sub": claims.get("sub"),
        "scope": claims.get("scope"),
        "iss": claims.get("iss"),
        "aud": claims.get("aud"),
        "exp": claims.get("exp"),
        "custom_claims": custom,
    }


def log_auth_context(cfg: Config, *, operation: str) -> None:
    if not verbose_enabled():
        return
    _debug(
        "%s profile=%r base_url=%r source=%r token_source=%s",
        operation,
        cfg.profile,
        cfg.base_url,
        cfg.source,
        token_source_label(cfg),
    )
    _debug(
        "%s client_id=%r m2m_scope=%r default_project=%r has_m2m=%s",
        operation,
        cfg.client_id,
        cfg.m2m_scope,
        cfg.default_project,
        cfg.has_m2m,
    )
    if cfg.client_secret:
        _debug("%s client_secret=%s", operation, redact(cfg.client_secret))
    if cfg.file_access_token:
        _debug(
            "%s persisted_token=%s expires_at=%s",
            operation,
            redact(cfg.file_access_token),
            cfg.token_expires_at,
        )


def log_bearer_token(access_token: str, *, operation: str) -> None:
    if not verbose_enabled():
        return
    _debug("%s bearer_token=%s", operation, redact(access_token))
    summary = jwt_claims_summary(access_token)
    _debug("%s jwt_claims=%s", operation, summary)
    sub = summary.get("sub") if isinstance(summary, dict) else None
    custom = summary.get("custom_claims") if isinstance(summary, dict) else None
    if (
        sub
        and isinstance(custom, dict)
        and not custom.get("org_id")
        and not custom.get("organization_id")
    ):
        _debug(
            "%s note: JWT has no org_id/organization_id claim; sum-api likely resolves org via "
            "Stytch GET /v1/m2m/clients/{sub} trusted_metadata.org_id",
            operation,
        )


def log_http_request(method: str, url: str) -> None:
    if not verbose_enabled():
        return
    _debug("HTTP %s %s", method, url)


def log_http_response(
    method: str,
    url: str,
    *,
    status: int,
    body: Any,
    headers: dict[str, str] | None = None,
) -> None:
    if not verbose_enabled():
        return
    hdrs = headers or {}
    request_id = hdrs.get("x-request-id") or hdrs.get("X-Request-Id")
    if isinstance(body, dict):
        request_id = request_id or body.get("request_id")
    _debug("HTTP %s %s -> %s request_id=%r", method, url, status, request_id)
    if status >= 400:
        _debug("HTTP error body=%s", body)


def log_api_error(status: int, body: Any, *, method: str, url: str) -> None:
    log_http_response(method, url, status=status, body=body)
    if not verbose_enabled():
        return
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
        code = body.get("code")
        if detail:
            _debug(
                "sum-api problem: code=%r detail=%r request_id=%r",
                code,
                detail,
                body.get("request_id"),
            )
    if status == 401 and isinstance(body, dict):
        detail = str(body.get("detail", ""))
        if "service principal not found" in detail.casefold():
            _debug(
                "whoami hint: token exchange can succeed while GET /v1/me fails if Stytch has no M2M client "
                "record for jwt.sub (client_id), or trusted_metadata.org_id is missing. "
                "Confirm client_id matches Summation admin and org-bound M2M for this base_url."
            )


def log_auth_error(message: str) -> None:
    if verbose_enabled():
        logger.debug("auth error: %s", message)
