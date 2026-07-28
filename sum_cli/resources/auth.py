"""`sumcli auth ...`"""

from __future__ import annotations

import httpx
import typer

from sum_cli import debug_log
from sum_cli.auth import (
    AuthError,
    complete_device_login,
    login_and_persist,
    persisted_token_valid,
    revoke_device_login_session,
    start_device_login,
)
from sum_cli.client import Client
from sum_cli.config_store import redact
from sum_cli.output import action, emit, emit_error, err, ok
from sum_cli.commands import ProfileOption, api_client, get_config, unwrap_data

app = typer.Typer(no_args_is_help=True)

_WHOAMI = action("Show identity", "sumcli auth whoami")
_CONFIG = action("Show config", "sumcli config active")
_LOGIN = action("Start device login", "sumcli auth login")
_LOGIN_M2M = action("Refresh M2M session", "sumcli auth login --m2m")


def _auth_mode(cfg) -> str:
    return "device_login" if cfg.device_login_credential else "m2m"


def _emit_device_login_prompt(start) -> None:
    typer.echo("Open this link in your browser to approve the Summation login:", err=True)
    typer.echo(start.verification_uri_complete, err=True)
    typer.echo(f"Verification code: {start.user_code}", err=True)
    typer.echo("Waiting for approval...", err=True)


@app.command("login")
def login(
    ctx: typer.Context,
    profile: ProfileOption = None,
    m2m: bool = typer.Option(
        False, "--m2m", help="Use M2M credentials instead of interactive device login."
    ),
) -> None:
    cfg = get_config(ctx, profile)

    if m2m:
        if not cfg.has_m2m:
            emit_error(
                err(
                    "CREDENTIALS_REQUIRED",
                    f"Profile '{cfg.profile}' needs client_id and client_secret for M2M login.",
                    "Run: sumcli config set-profile <name> --base-url ... --client-id ... --client-secret ...",
                    next_actions=[
                        action("Create profile", "sumcli config set-profile <name> --base-url ..."),
                        _CONFIG,
                    ],
                )
            )
        try:
            result, path = login_and_persist(cfg)
        except AuthError as exc:
            emit_error(
                err(
                    "AUTH_LOGIN_FAILED",
                    str(exc),
                    "Check base_url and M2M credentials, then retry.",
                    next_actions=[_CONFIG],
                )
            )
        emit(
            ok(
                {
                    "profile": cfg.profile,
                    "path": str(path),
                    "auth_mode": "m2m",
                    "access_token": redact(result.access_token),
                    "token_expires_at": result.expires_at_wall,
                    "persisted": True,
                },
                next_actions=[_WHOAMI, action("List projects", "sumcli projects list")],
            )
        )
        return

    try:
        with httpx.Client(timeout=15.0) as http:
            start = start_device_login(cfg, http)
            _emit_device_login_prompt(start)
            result = complete_device_login(cfg, start, http)
    except AuthError as exc:
        emit_error(
            err(
                "AUTH_LOGIN_FAILED",
                str(exc),
                "Check the base_url and retry the device login flow.",
                next_actions=[_CONFIG],
            )
        )

    if result.status == "denied":
        emit_error(
            err(
                "DEVICE_LOGIN_DENIED",
                "The Summation device login was denied.",
                "Run sumcli auth login to start a fresh device login.",
                next_actions=[_LOGIN, _CONFIG],
            )
        )
    if result.status == "expired":
        emit_error(
            err(
                "DEVICE_LOGIN_EXPIRED",
                "The Summation device login expired before approval completed.",
                "Run sumcli auth login to start a fresh device login.",
                next_actions=[_LOGIN, _CONFIG],
            )
        )
    if result.verification_error:
        emit_error(
            err(
                "AUTH_LOGIN_VERIFICATION_FAILED",
                result.verification_error,
                "The device-login credential was stored, but verification failed. Run sumcli auth whoami to retry verification.",
                next_actions=[_WHOAMI, _CONFIG],
                data={
                    "profile": result.profile,
                    "path": str(result.config_path) if result.config_path else None,
                },
            )
        )
    emit(
        ok(
            {
                "profile": result.profile,
                "path": str(result.config_path) if result.config_path else None,
                "auth_mode": "device_login",
                "identity": result.identity.__dict__ if result.identity else None,
                "verified_identity": result.verified_identity,
                "persisted": True,
            },
            next_actions=[_WHOAMI, action("List projects", "sumcli projects list")],
        )
    )


@app.command("logout")
def logout(ctx: typer.Context, profile: ProfileOption = None) -> None:
    cfg = get_config(ctx, profile)
    try:
        with httpx.Client(timeout=15.0) as http:
            result = revoke_device_login_session(cfg, http)
    except AuthError as exc:
        emit_error(
            err(
                "AUTH_LOGOUT_FAILED",
                str(exc),
                "Make sure this profile has a stored device-login credential, then retry.",
                next_actions=[_CONFIG, _LOGIN],
            )
        )
    emit(
        ok(
            {
                "profile": result.profile,
                "path": str(result.config_path),
                "auth_mode": "device_login",
                "revoked": result.success,
                "cleared_local_credential": True,
            },
            next_actions=[_LOGIN, _CONFIG],
        )
    )


@app.command("whoami")
def whoami(ctx: typer.Context, profile: ProfileOption = None) -> None:
    cfg = get_config(ctx, profile)
    debug_log.log_auth_context(cfg, operation="auth whoami")
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/me")
    identity = unwrap_data(body or {}, "data") or body
    if isinstance(identity, dict) and "identity" in identity:
        identity = identity["identity"]
    session_valid = cfg.token_expires_at is not None and persisted_token_valid(cfg.token_expires_at)
    emit(
        ok(
            {
                "profile": cfg.profile,
                "base_url": cfg.base_url,
                "auth_mode": _auth_mode(cfg),
                "identity": identity,
                "session_persisted": bool(cfg.device_login_credential) or session_valid,
            },
            next_actions=[
                action("Switch profile", "sumcli config use <profile>"),
                action("List projects", "sumcli projects list"),
            ],
        )
    )


@app.command("status")
def status(ctx: typer.Context, profile: ProfileOption = None) -> None:
    cfg = get_config(ctx, profile)
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/auth/status")
    data = unwrap_data(body or {}, "data") or body
    authenticated = data.get("authenticated", False) if isinstance(data, dict) else False
    if not authenticated:
        emit_error(
            err(
                "NOT_AUTHENTICATED",
                "Not authenticated.",
                "Run sumcli auth login, or sumcli auth login --m2m if this profile uses machine credentials.",
                next_actions=[_LOGIN, _LOGIN_M2M, _CONFIG],
            )
        )
    emit(
        ok(
            {"status": data, "profile": cfg.profile},
            next_actions=[_WHOAMI],
        )
    )


@app.command("token")
def show_token(ctx: typer.Context, profile: ProfileOption = None) -> None:
    cfg = get_config(ctx, profile)
    with Client(cfg) as c:
        token = c.token()
    emit(
        ok(
            {
                "profile": cfg.profile,
                "auth_mode": _auth_mode(cfg),
                "access_token": redact(token),
                "token_length": len(token),
                "token_expires_at": cfg.token_expires_at,
                "session_persisted": bool(cfg.device_login_credential)
                or (
                    cfg.token_expires_at is not None and persisted_token_valid(cfg.token_expires_at)
                ),
            },
            next_actions=[_WHOAMI, _LOGIN],
        )
    )
