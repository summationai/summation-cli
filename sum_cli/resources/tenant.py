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


def _fetch_targetable(ctx: typer.Context, profile: str | None) -> dict:
    """The org-discovery envelope, fetched as the caller's HOME identity. Discovery must not carry the
    resolved-org override: after `tenant use org-y`, if org-y is deactivated or the role is lost, an
    override-scoped call fails first and both `tenant list` and `tenant use` would error with no way
    back but `--clear`. Requesting the max page keeps `tenant use` validation whole for operators."""
    with api_client(ctx, profile, include_resolved_org=False) as c:
        body = c.request("GET", "/v1/tenant/orgs", params={"limit": 500})
    data = unwrap_data(body or {}, "data")
    return data if isinstance(data, dict) else {}


@app.command("list")
def list_tenants(ctx: typer.Context, profile: ProfileOption = None) -> None:
    """List the organizations you can act in. An internal multi-tenant operator sees every tenant;
    everyone else sees only their own org. Pick one with `sumcli tenant use <org_id>`."""
    data = _fetch_targetable(ctx, profile)
    orgs = data.get("orgs") or []
    emit(
        ok(
            {
                "orgs": orgs,
                "total": data.get("total", len(orgs)),
                "showing": data.get("showing", len(orgs)),
                "truncated": data.get("truncated", False),
            },
            next_actions=[action("Target one for later calls", "sumcli tenant use <org_id>")],
        )
    )


@app.command("use")
def use_tenant(
    ctx: typer.Context,
    org_id: Annotated[
        str | None, typer.Argument(help="Organization id to target on later calls.")
    ] = None,
    clear: Annotated[
        bool, typer.Option("--clear", help="Stop targeting; act in your home org again.")
    ] = False,
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
    targetable = {o.get("org_id") for o in _fetch_targetable(ctx, profile).get("orgs") or []}
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
