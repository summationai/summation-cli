"""`sumcli tenant ...`"""

from __future__ import annotations

import typer

from sum_cli.output import action, emit, ok
from sum_cli.commands import ProfileOption, api_client, unwrap_data

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
