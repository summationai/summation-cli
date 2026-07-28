"""Acquire bearer tokens for sum-api.

Credentials come only from profiles in ~/.summation/config. A profile may carry a
persisted device-login credential, a persisted M2M access token, or M2M client
credentials. Runtime precedence is device-login credential first, then persisted
M2M token, then fresh M2M exchange. ``SUM_API_*`` environment variables are read
only by ``config import-env``.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import httpx

from sum_cli import debug_log
from sum_cli.config import Config
from sum_cli.config_store import update_profile_field
from sum_cli.constants import (
    DEVICE_LOGIN_CREDENTIAL_KEY,
    DEFAULT_M2M_TTL_SECONDS,
    TOKEN_CACHE_SKEW_SECONDS,
    TOKEN_EXPIRES_AT_KEY,
)

logger = logging.getLogger(__name__)


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    expires_at: float  # time.monotonic() when token should be refreshed
    expires_at_wall: float | None = None  # unix epoch seconds


@dataclass(frozen=True)
class DeviceLoginStartResult:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DeviceLoginIdentity:
    external_member_id: str | None = None
    email: str | None = None
    organization_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True)
class DeviceLoginPollResult:
    status: Literal["pending", "approved", "denied", "expired"]
    credential: str | None = None
    identity: DeviceLoginIdentity | None = None


@dataclass(frozen=True)
class DeviceLoginCompleteResult:
    status: Literal["approved", "denied", "expired"]
    profile: str
    config_path: Path | None = None
    identity: DeviceLoginIdentity | None = None
    verified_identity: dict[str, Any] | None = None
    verification_error: str | None = None


@dataclass(frozen=True)
class DeviceLoginLogoutResult:
    success: bool
    profile: str
    config_path: Path


def _jwt_exp_wall_time(access_token: str) -> float | None:
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return None


def m2m_token_expires_at_wall(body: dict, access_token: str) -> float:
    """Wall-clock expiry for persisting in TOML."""
    now = time.time()
    expires_in = body.get("expires_in")
    if expires_in is not None:
        try:
            return now + float(expires_in)
        except (TypeError, ValueError):
            pass
    expires_at = body.get("expires_at")
    if expires_at is not None:
        try:
            return float(expires_at)
        except (TypeError, ValueError):
            pass
    jwt_exp = _jwt_exp_wall_time(access_token)
    if jwt_exp is not None:
        return jwt_exp
    logger.warning(
        "M2M response missing expires_in; using %ss default TTL",
        DEFAULT_M2M_TTL_SECONDS,
    )
    return now + DEFAULT_M2M_TTL_SECONDS


def m2m_token_expires_at_monotonic(body: dict, access_token: str) -> float:
    """Monotonic expiry for in-process cache."""
    wall = m2m_token_expires_at_wall(body, access_token)
    return time.monotonic() + max(0.0, wall - time.time())


def persisted_token_valid(expires_at_wall: float | None) -> bool:
    if expires_at_wall is None:
        return False
    return time.time() < expires_at_wall - TOKEN_CACHE_SKEW_SECONDS


def token_result_from_persisted(access_token: str, expires_at_wall: float) -> TokenResult:
    remaining = max(0.0, expires_at_wall - time.time())
    return TokenResult(
        access_token=access_token,
        expires_at=time.monotonic() + remaining,
        expires_at_wall=expires_at_wall,
    )


def token_result_from_device_login_credential(credential: str) -> TokenResult:
    # Device-login credentials are opaque session bearers. We do not derive a local
    # expiry for them here; the server remains the source of truth for validity.
    return TokenResult(
        access_token=credential,
        expires_at=float("inf"),
        expires_at_wall=None,
    )


def _response_json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError(f"{operation} response was not valid JSON.") from exc
    if not isinstance(body, dict):
        raise AuthError(f"{operation} response was not a JSON object.")
    return body


def _optional_device_login_identity(value: Any) -> DeviceLoginIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AuthError("Device login poll response field identity must be a JSON object.")
    return DeviceLoginIdentity(
        external_member_id=value.get("external_member_id")
        if isinstance(value.get("external_member_id"), str)
        else None,
        email=value.get("email") if isinstance(value.get("email"), str) else None,
        organization_id=value.get("organization_id")
        if isinstance(value.get("organization_id"), str)
        else None,
        user_id=value.get("user_id") if isinstance(value.get("user_id"), str) else None,
    )


def start_device_login(
    cfg: Config,
    http: httpx.Client,
    *,
    surface: str = "sumcli",
) -> DeviceLoginStartResult:
    url = f"{cfg.base_url}/v1/auth/device-logins"
    payload = {"surface": surface}
    debug_log.log_http_request("POST", url)
    response = http.post(url, json=payload, timeout=15)
    if response.status_code >= 400:
        raise AuthError(f"Device login start failed ({response.status_code}): {response.text}")
    body = _response_json_object(response, operation="Device login start")
    debug_log.log_http_response(
        "POST",
        url,
        status=response.status_code,
        body={
            "user_code": body.get("user_code"),
            "verification_uri": body.get("verification_uri"),
            "verification_uri_complete": body.get("verification_uri_complete"),
            "expires_in": body.get("expires_in"),
            "interval": body.get("interval"),
        },
    )
    return DeviceLoginStartResult(
        device_code=body["device_code"],
        user_code=body["user_code"],
        verification_uri=body["verification_uri"],
        verification_uri_complete=body["verification_uri_complete"],
        expires_in=body["expires_in"],
        interval=body["interval"],
    )


def poll_device_login_once(
    cfg: Config,
    http: httpx.Client,
    *,
    device_code: str,
) -> DeviceLoginPollResult:
    url = f"{cfg.base_url}/v1/auth/device-logins/tokens"
    debug_log.log_http_request("POST", url)
    response = http.post(url, json={"device_code": device_code}, timeout=15)
    if response.status_code >= 400:
        raise AuthError(f"Device login poll failed ({response.status_code}): {response.text}")
    body = _response_json_object(response, operation="Device login poll")
    status = body["status"].lower()
    if status not in {"pending", "approved", "denied", "expired"}:
        raise AuthError(f"Device login poll returned unknown status: {status}")
    credential = body.get("credential")
    if credential is not None and not isinstance(credential, str):
        raise AuthError("Device login poll response field credential must be a string.")
    return DeviceLoginPollResult(
        status=status,
        credential=credential,
        identity=_optional_device_login_identity(body.get("identity")),
    )


def persist_device_login_session(cfg: Config, credential: str) -> Path:
    return update_profile_field(
        cfg.profile,
        **{
            DEVICE_LOGIN_CREDENTIAL_KEY: credential,
            "access_token": None,
            TOKEN_EXPIRES_AT_KEY: None,
        },
    )


def clear_device_login_session(cfg: Config) -> Path:
    return update_profile_field(cfg.profile, **{DEVICE_LOGIN_CREDENTIAL_KEY: None})


def verify_device_login_credential(
    cfg: Config,
    http: httpx.Client,
    *,
    credential: str,
) -> dict[str, Any]:
    url = f"{cfg.base_url}/v1/me"
    debug_log.log_http_request("GET", url)
    response = http.get(url, headers={"Authorization": f"Bearer {credential}"}, timeout=15)
    if response.status_code >= 400:
        raise AuthError(
            f"Device login verification failed ({response.status_code}): {response.text}"
        )
    body = _response_json_object(response, operation="Device login verification")
    debug_log.log_http_response("GET", url, status=response.status_code, body=None)
    return body


def complete_device_login(
    cfg: Config,
    start: DeviceLoginStartResult,
    http: httpx.Client,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> DeviceLoginCompleteResult:
    deadline = monotonic() + start.expires_in
    while True:
        poll = poll_device_login_once(cfg, http, device_code=start.device_code)
        if poll.status == "pending":
            remaining = deadline - monotonic()
            if remaining <= 0:
                return DeviceLoginCompleteResult(status="expired", profile=cfg.profile)
            sleep(min(start.interval, remaining))
            continue
        if poll.status in {"denied", "expired"}:
            return DeviceLoginCompleteResult(
                status=poll.status,
                profile=cfg.profile,
                identity=poll.identity,
            )
        if not poll.credential:
            raise AuthError("Device login poll approved but did not return a credential.")
        path = persist_device_login_session(cfg, poll.credential)
        verified_identity: dict[str, Any] | None = None
        verification_error: str | None = None
        try:
            verified_identity = verify_device_login_credential(
                cfg, http, credential=poll.credential
            )
        except AuthError as exc:
            verification_error = str(exc)
        return DeviceLoginCompleteResult(
            status="approved",
            profile=cfg.profile,
            config_path=path,
            identity=poll.identity,
            verified_identity=verified_identity,
            verification_error=verification_error,
        )


def revoke_device_login_session(cfg: Config, http: httpx.Client) -> DeviceLoginLogoutResult:
    credential = cfg.device_login_credential
    if not credential:
        raise AuthError(f"Profile '{cfg.profile}' does not have a stored device-login credential.")
    url = f"{cfg.base_url}/v1/auth/device-logins/revoke"
    debug_log.log_http_request("POST", url)
    response = http.post(url, headers={"Authorization": f"Bearer {credential}"}, timeout=15)
    if response.status_code >= 400:
        raise AuthError(f"Device login logout failed ({response.status_code}): {response.text}")
    body = _response_json_object(response, operation="Device login logout")
    debug_log.log_http_response("POST", url, status=response.status_code, body=body)
    path = clear_device_login_session(cfg)
    return DeviceLoginLogoutResult(success=body["success"], profile=cfg.profile, config_path=path)


def persist_m2m_session(cfg: Config, result: TokenResult) -> Path:
    """Persist the refreshed bearer token on the profile in ~/.summation/config.

    Only the token fields are written. base_url/client_id/client_secret/m2m_scope are
    owned by ``config set-profile`` / ``config import-env``; a token refresh must not
    overwrite them with resolved values (e.g. an env-derived or default base_url).
    """
    if not cfg.has_m2m:
        raise AuthError(
            "Cannot persist session without client_id and client_secret on the profile."
        )
    if result.expires_at_wall is None:
        raise AuthError("Internal error: M2M token missing wall-clock expiry.")
    return update_profile_field(
        cfg.profile,
        access_token=result.access_token,
        **{TOKEN_EXPIRES_AT_KEY: str(int(result.expires_at_wall))},
    )


def exchange_m2m_token(cfg: Config, http: httpx.Client) -> TokenResult:
    if not cfg.has_m2m:
        raise AuthError(
            "M2M credentials required. Run: sumcli config set-profile <name> "
            "--base-url ... --client-id ... --client-secret ... "
            "then: sumcli auth login --m2m"
        )
    payload: dict[str, str] = {
        "client_id": cfg.client_id or "",
        "client_secret": cfg.client_secret or "",
    }
    if cfg.m2m_scope:
        payload["scope"] = cfg.m2m_scope
    url = f"{cfg.base_url}/v1/auth/m2m/token"
    debug_log.log_http_request("POST", url)
    resp = http.post(url, data=payload, timeout=15)
    if resp.status_code >= 400:
        try:
            err_body: Any = resp.json()
        except ValueError:
            err_body = resp.text
        debug_log.log_http_response("POST", url, status=resp.status_code, body=err_body)
        raise AuthError(f"M2M token exchange failed ({resp.status_code}): {resp.text}")
    debug_log.log_http_response(
        "POST", url, status=resp.status_code, body={"access_token": "(redacted)"}
    )
    body = resp.json()
    if not isinstance(body, dict):
        raise AuthError(f"M2M response not a JSON object: {body!r}")
    token = body.get("access_token")
    if not token or not isinstance(token, str):
        raise AuthError(f"M2M response missing access_token: {body!r}")
    wall = m2m_token_expires_at_wall(body, token)
    return TokenResult(
        access_token=token,
        expires_at=time.monotonic() + max(0.0, wall - time.time()),
        expires_at_wall=wall,
    )


def login_and_persist(cfg: Config, http: httpx.Client | None = None) -> tuple[TokenResult, Path]:
    """Exchange M2M credentials and persist the session on ``cfg.profile``."""
    own_client = http is None
    client = http or httpx.Client(timeout=15.0)
    try:
        result = exchange_m2m_token(cfg, client)
        path = persist_m2m_session(cfg, result)
        return result, path
    finally:
        if own_client:
            client.close()


def acquire_token(cfg: Config, http: httpx.Client, *, persist: bool = True) -> TokenResult:
    if cfg.device_login_credential:
        debug_log.debug("acquire_token: using device_login_credential from profile %r", cfg.profile)
        return token_result_from_device_login_credential(cfg.device_login_credential)

    file_token = cfg.file_access_token
    if (
        file_token
        and cfg.token_expires_at is not None
        and persisted_token_valid(cfg.token_expires_at)
    ):
        debug_log.debug("acquire_token: using persisted access_token from profile %r", cfg.profile)
        return token_result_from_persisted(file_token, cfg.token_expires_at)

    if cfg.has_m2m:
        debug_log.debug(
            "acquire_token: exchanging M2M at %s/v1/auth/m2m/token client_id=%r scope=%r",
            cfg.base_url,
            cfg.client_id,
            cfg.m2m_scope,
        )
        result = exchange_m2m_token(cfg, http)
        if persist:
            persist_m2m_session(cfg, result)
        return result

    raise AuthError(
        "No credentials for this profile. Run sumcli auth login, or set client_id and client_secret and run: sumcli auth login --m2m"
    )


def token_cache_valid(expires_at: float) -> bool:
    return time.monotonic() < expires_at - TOKEN_CACHE_SKEW_SECONDS
