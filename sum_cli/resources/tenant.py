"""`sumcli tenant ...`"""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.config_store import update_profile_field
from sum_cli.output import action, emit, emit_error, err, ok
from sum_cli.commands import ProfileOption, api_client, get_config, unwrap_data

app = typer.Typer(no_args_is_help=True)


@app.command("show")
def show_tenant(ctx: typer.Context, profile: ProfileOption = None) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/tenant/org")
    emit(
        ok(
            {"organization": unwrap_data(body or {}, "data") or body},
            next_actions=[action("Show identity", "sumcli auth whoami")],
        )
    )


def _list_targetable_orgs(ctx: typer.Context, profile: str | None) -> list[dict]:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/tenant/orgs")
    data = unwrap_data(body or {}, "data") or {}
    orgs = data.get("orgs") if isinstance(data, dict) else None
    return orgs or []


@app.command("list")
def list_tenants(ctx: typer.Context, profile: ProfileOption = None) -> None:
    """List the organizations you can act in. An internal multi-tenant operator sees every tenant;
    everyone else sees only their own org. Pick one with `sumcli tenant use <org_id>`."""
    orgs = _list_targetable_orgs(ctx, profile)
    emit(
        ok(
            {"orgs": orgs, "total": len(orgs)},
            next_actions=[action("Target one for later calls", "sumcli tenant use <org_id>")],
        )
    )


@app.command("use")
def use_tenant(
    ctx: typer.Context,
    org_id: Annotated[str | None, typer.Argument(help="Organization id to target on later calls.")] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Stop targeting; act in your home org again.")] = False,
    profile: ProfileOption = None,
) -> None:
    """Set (or clear) the organization later calls target, persisted on the active profile.

    Equivalent to passing `--org <org_id>` on every call. Only internal multi-tenant operators may
    target an org other than their own; the id is validated against `tenant list` before it is saved.
    """
    resolved_profile = get_config(ctx, profile).profile
    if clear:
        update_profile_field(resolved_profile, resolved_org=None)
        emit(ok({"resolved_org": None, "profile": resolved_profile}))
        return
    if not org_id:
        emit_error(
            err(
                "ORG_REQUIRED",
                "Pass an organization id to target, or --clear to act in your home org.",
                "Run `sumcli tenant list` to see the orgs you can target.",
            )
        )
    targetable = {o.get("org_id") for o in _list_targetable_orgs(ctx, profile)}
    if org_id not in targetable:
        emit_error(
            err(
                "ORG_NOT_TARGETABLE",
                f"{org_id} is not among the organizations you can target.",
                "Run `sumcli tenant list`; you must be a member, or an internal operator, to target it.",
            )
        )
    update_profile_field(resolved_profile, resolved_org=org_id)
    emit(
        ok(
            {"resolved_org": org_id, "profile": resolved_profile},
            next_actions=[action("Confirm identity", "sumcli auth whoami")],
        )
    )
