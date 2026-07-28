"""`sumcli grid ...`"""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.output import emit, ok
from sum_cli.commands import ProfileOption, api_client, unwrap_data
from sum_cli.stream_options import (
    FollowOption,
    WaitOption,
    post_with_wait_follow,
)

app = typer.Typer(no_args_is_help=True)


@app.command("status")
def grid_status(ctx: typer.Context, profile: ProfileOption = None) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/grid/status")
    emit(ok({"status": unwrap_data(body or {}, "data") or body}))


@app.command("diff")
def grid_diff(ctx: typer.Context, profile: ProfileOption = None) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/grid/diff")
    emit(ok({"diff": unwrap_data(body or {}, "data") or body}))


@app.command("validate")
def grid_validate(ctx: typer.Context, profile: ProfileOption = None) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/grid/validation")
    emit(ok({"validation": unwrap_data(body or {}, "data") or body}))


@app.command("push")
def grid_push(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    wait: WaitOption = True,
    follow: FollowOption = False,
    profile: ProfileOption = None,
) -> None:
    path = f"/v1/grid/tables/{table_id}/synchronizations"
    with api_client(ctx, profile) as c:
        outcome = post_with_wait_follow(
            c,
            "POST",
            path,
            wait=wait,
            follow=follow,
            json={},
            result_builder=lambda p, t: {"payload": p, "text": t},
        )
        if outcome.streamed:
            return
        body = outcome.body
    emit(ok({"sync": unwrap_data(body or {}, "data") or body}))


@app.command("lineage")
def grid_lineage(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/grid/tables/{table_id}/lineage")
    emit(ok({"lineage": unwrap_data(body or {}, "data") or body}))


@app.command("create")
def grid_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    query: Annotated[str, typer.Option("--query")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            "/v1/grid/tables",
            json={"name": name, "query": query},
        )
    emit(ok({"table": unwrap_data(body or {}, "data") or body}))


@app.command("materialize")
def grid_materialize(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    profile: ProfileOption = None,
) -> None:
    params = {"dry_run": True} if dry_run else None
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/grid/tables/{table_id}/materialize",
            params=params,
        )
    emit(ok({"materialize": unwrap_data(body or {}, "data") or body}))
