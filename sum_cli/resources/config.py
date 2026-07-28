"""`sumcli config ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sum_cli.auth import AuthError, login_and_persist
from sum_cli.commands import ProfileOption, get_config
from sum_cli.config import _normalize_base_url, load
from sum_cli.config_store import (
    config_path,
    get_active_profile_name,
    read_all,
    redact,
    set_active_profile,
    update_profile_field,
    write_all,
)
from sum_cli.constants import ACTIVE_PROFILE_KEY, META_SECTION, SECRET_KEYS
from sum_cli.env_import import (
    EnvImportError,
    parse_env_file,
    profile_section_from_env,
    required_fields_present,
)
from sum_cli.output import action, emit, emit_error, err, ok, param
from sum_cli.profile_meta import account_summary, profile_list_item
from sum_cli.project_context import resolve_project

app = typer.Typer(no_args_is_help=True)


@app.command("import-env")
def import_env(
    env_file: Annotated[Path, typer.Argument(help="Skill-style env file (.summation-config).")],
    profile: Annotated[str, typer.Option("--profile", help="Profile name to create or replace.")],
    activate: Annotated[
        bool,
        typer.Option("--activate/--no-activate", help="Set as active profile."),
    ] = False,
    login: Annotated[
        bool,
        typer.Option("--login/--no-login", help="Exchange M2M and persist access_token."),
    ] = True,
) -> None:
    path = config_path()
    try:
        raw = parse_env_file(env_file.expanduser())
    except FileNotFoundError:
        emit_error(
            err(
                "FILE_NOT_FOUND",
                f"Env file not found: {env_file}",
                "Pass a path to .summation-config or similar.",
            )
        )
    except EnvImportError as exc:
        emit_error(err(exc.code, exc.message, exc.hint))
    section = profile_section_from_env(raw)
    missing = required_fields_present(section)
    if missing:
        emit_error(
            err(
                "CREDENTIALS_REQUIRED",
                f"Missing required keys in {env_file}: {', '.join(missing)}",
                "Include SUM_API_CLIENT_ID and SUM_API_CLIENT_SECRET.",
            )
        )
    data = read_all(path)
    data[profile] = section
    write_all(path, data)
    if activate:
        set_active_profile(profile)

    login_result: dict | None = None
    if login:
        cfg = load(profile=profile, config_file=path)
        try:
            token_result, path = login_and_persist(cfg)
            login_result = {
                "access_token": redact(token_result.access_token),
                "token_expires_at": token_result.expires_at_wall,
            }
        except AuthError as exc:
            emit_error(
                err(
                    "AUTH_LOGIN_FAILED",
                    str(exc),
                    "Profile imported without a session. Run: sumcli auth login --m2m",
                    next_actions=[
                        action("Login (M2M)", f"sumcli --profile {profile} auth login --m2m")
                    ],
                )
            )

    emit(
        ok(
            {
                "profile": profile,
                "path": str(path),
                "imported_from": str(env_file.expanduser()),
                "activated": activate,
                "login": login_result,
            },
            next_actions=[
                action("Whoami", f"sumcli --profile {profile} auth whoami"),
                action("Show profile", f"sumcli config show {profile}"),
            ],
        )
    )


@app.command("path")
def show_path() -> None:
    path = config_path()
    emit(ok({"path": str(path)}, next_actions=[action("List profiles", "sumcli config list")]))


@app.command("list")
def list_profiles() -> None:
    path = config_path()
    data = read_all(path)
    active = get_active_profile_name(data)
    profiles = [k for k in data if k != META_SECTION]
    items = [profile_list_item(n, data[n], active=n == active) for n in sorted(profiles)]
    emit(
        ok(
            {"profiles": items, "path": str(path), "active_profile": active},
            next_actions=[
                action("Show active config", "sumcli config active"),
            ],
        )
    )


@app.command("show")
def show_profile(
    profile: Annotated[str | None, typer.Argument()] = None,
) -> None:
    path = config_path()
    data = read_all(path)
    cfg = load(profile=profile)
    section = data.get(cfg.profile)
    if section is None:
        emit_error(
            err(
                "PROFILE_NOT_FOUND",
                f"Profile '{cfg.profile}' not in {path}.",
                "Run sumcli config set-profile to create it.",
            )
        )
    redacted = {k: (redact(v) if k in SECRET_KEYS else v) for k, v in section.items()}
    emit(ok({"profile": cfg.profile, "values": redacted, "path": str(path)}))


@app.command("active")
def show_active(ctx: typer.Context, profile: ProfileOption = None) -> None:
    cfg = get_config(ctx, profile)
    summary = account_summary(cfg, default_project=resolve_project(cfg))
    emit(
        ok(
            {
                **summary,
                "client_id": cfg.client_id,
                "client_secret": redact(cfg.client_secret) if cfg.client_secret else None,
                "access_token": redact(cfg.access_token) if cfg.access_token else None,
                "device_login_credential": redact(cfg.device_login_credential)
                if cfg.device_login_credential
                else None,
                "sources": cfg.source,
            },
            next_actions=[
                action("Whoami", "sumcli auth whoami"),
                action("Switch profile", "sumcli config use <profile>"),
            ],
        )
    )


@app.command("use")
def use_profile(
    name: Annotated[str, typer.Argument(help="Profile name (environment account).")],
    project: Annotated[str | None, typer.Option("--project", help="Default project ID.")] = None,
) -> None:
    path = config_path()
    data = read_all(path)
    if name not in data or name == META_SECTION:
        emit_error(
            err(
                "PROFILE_NOT_FOUND",
                f"No profile '{name}' in {path}.",
                "Run sumcli config set-profile to add it.",
            )
        )
    set_active_profile(name)
    if project is not None:
        update_profile_field(name, default_project=project)
    cfg = load(profile=name)
    result = account_summary(cfg, default_project=project or cfg.default_project)
    next_actions = [
        action("Whoami", "sumcli auth whoami"),
        action("List projects", "sumcli projects list"),
    ]
    if result.get("default_project"):
        pid = result["default_project"]
        next_actions.append(
            action(
                "Show default project",
                f"sumcli projects show {pid}",
                params={"project-id": param("Project ID", value=pid)},
            )
        )
    emit(ok({**result, "path": str(path)}, next_actions=next_actions))


@app.command("set-project")
def set_project(
    ctx: typer.Context,
    project: Annotated[str, typer.Option("--project", help="Project ID.")],
    profile: ProfileOption = None,
) -> None:
    cfg = get_config(ctx, profile)
    update_profile_field(cfg.profile, default_project=project)
    emit(
        ok(
            account_summary(cfg, default_project=project),
            next_actions=[
                action("List projects", "sumcli projects list"),
                action(
                    "Show project",
                    f"sumcli projects show {project}",
                    params={"project-id": param("Project ID", value=project)},
                ),
            ],
        )
    )


@app.command("clear-project")
def clear_project(ctx: typer.Context, profile: ProfileOption = None) -> None:
    cfg = get_config(ctx, profile)
    update_profile_field(cfg.profile, default_project=None)
    emit(
        ok(
            account_summary(cfg, default_project=None),
            next_actions=[action("List projects", "sumcli projects list")],
        )
    )


@app.command("set-profile")
def set_profile(
    name: Annotated[str, typer.Argument()],
    base_url: Annotated[str, typer.Option("--base-url")],
    client_id: Annotated[str | None, typer.Option("--client-id")] = None,
    client_secret: Annotated[str | None, typer.Option("--client-secret", hide_input=True)] = None,
    default_project: Annotated[str | None, typer.Option("--default-project")] = None,
    m2m_scope: Annotated[str | None, typer.Option("--m2m-scope")] = None,
    login: Annotated[
        bool,
        typer.Option(
            "--login/--no-login",
            help="Exchange M2M and persist access_token when client credentials are provided.",
        ),
    ] = True,
) -> None:
    normalized_client_id = client_id.strip() if client_id else None
    normalized_client_secret = client_secret or None
    has_m2m_credentials = bool(normalized_client_id and normalized_client_secret)

    if bool(normalized_client_id) != bool(normalized_client_secret):
        emit_error(
            err(
                "CREDENTIALS_REQUIRED",
                "set-profile requires both --client-id and --client-secret together for M2M profiles.",
                "Pass both flags for M2M, or omit both and use sumcli auth login for device login.",
            )
        )

    path = config_path()
    data = read_all(path)
    section: dict[str, str] = {
        "base_url": _normalize_base_url(base_url),
    }
    if normalized_client_id and normalized_client_secret:
        section["client_id"] = normalized_client_id
        section["client_secret"] = normalized_client_secret
    if default_project:
        section["default_project"] = default_project
    if m2m_scope:
        section["m2m_scope"] = m2m_scope
    data[name] = section
    write_all(path, data)

    login_result: dict | None = None
    if login and has_m2m_credentials:
        cfg = load(profile=name, config_file=path)
        try:
            token_result, path = login_and_persist(cfg)
            login_result = {
                "access_token": redact(token_result.access_token),
                "token_expires_at": token_result.expires_at_wall,
            }
        except AuthError as exc:
            emit_error(
                err(
                    "AUTH_LOGIN_FAILED",
                    str(exc),
                    "Profile saved without a session. Run: sumcli auth login --m2m",
                    next_actions=[
                        action("Login (M2M)", f"sumcli --profile {name} auth login --m2m"),
                    ],
                )
            )

    login_action = (
        action("Login (M2M)", f"sumcli --profile {name} auth login --m2m")
        if has_m2m_credentials
        else action("Start device login", f"sumcli --profile {name} auth login")
    )
    next_actions = [
        action("Activate profile", f"sumcli config use {name}"),
        action("Whoami", f"sumcli --profile {name} auth whoami"),
    ]
    if login_result is None:
        next_actions.insert(0, login_action)

    emit(
        ok(
            {
                "profile": name,
                "path": str(path),
                "has_m2m_credentials": has_m2m_credentials,
                "login": login_result,
            },
            next_actions=next_actions,
        )
    )


@app.command("copy-profile")
def copy_profile(
    src: Annotated[str, typer.Argument(help="Source profile.")],
    dst: Annotated[str, typer.Argument(help="New profile name.")],
) -> None:
    path = config_path()
    data = read_all(path)
    if src not in data or src == META_SECTION:
        emit_error(
            err("PROFILE_NOT_FOUND", f"Source '{src}' not found.", "Check sumcli config list.")
        )
    data[dst] = dict(data[src])
    write_all(path, data)
    emit(
        ok(
            {"copied": src, "to": dst, "path": str(path)},
            next_actions=[action("Use new profile", f"sumcli config use {dst}")],
        )
    )


@app.command("delete-profile")
def delete_profile(
    name: Annotated[str, typer.Argument()],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    if not confirm:
        emit_error(
            err("CONFIRM_REQUIRED", "Pass --confirm to delete profile.", "Re-run with --confirm.")
        )
    path = config_path()
    data = read_all(path)
    if name not in data or name == META_SECTION:
        emit_error(
            err("PROFILE_NOT_FOUND", f"Profile '{name}' not found.", "Check sumcli config list.")
        )
    del data[name]
    meta = data.get(META_SECTION, {})
    if meta.get(ACTIVE_PROFILE_KEY) == name:
        meta.pop(ACTIVE_PROFILE_KEY, None)
        data[META_SECTION] = meta
    write_all(path, data)
    emit(ok({"deleted": name}, next_actions=[action("List profiles", "sumcli config list")]))
