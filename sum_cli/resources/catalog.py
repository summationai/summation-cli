"""`sumcli catalog ...` (catalog-entries API)."""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.output import emit, ok, truncate_list
from sum_cli.commands import (
    ProfileOption,
    api_client,
    extract_list,
    require_confirm,
    api_confirm_params,
    require_project,
    unwrap_data,
)

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_catalog(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option("--project")] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/catalog-entries")
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "items")
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "entries": listed["items"],
                "project_id": pid,
                **{k: v for k, v in listed.items() if k != "items"},
            }
        )
    )


@app.command("show")
def show_catalog(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/catalog-entries/{file_id}")
    emit(ok({"entry": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("attach")
def attach_catalog(
    ctx: typer.Context,
    source_type: Annotated[str, typer.Option("--source-type", help="table or view")],
    source_id: Annotated[str, typer.Option("--source-id")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/projects/{pid}/catalog-entries",
            json={"source_type": source_type, "source_id": source_id},
        )
    emit(ok({"entry": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("refresh")
def refresh_catalog(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/projects/{pid}/catalog-entries/{entry_id}/refreshes")
    emit(ok({"refresh": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("detach")
def detach_catalog(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="catalog detach")
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request(
            "DELETE",
            f"/v1/projects/{pid}/catalog-entries/{file_id}",
            params=api_confirm_params(),
        )
    emit(
        ok(
            {
                "detached": file_id,
                "result": unwrap_data(body or {}, "data") or body,
                "project_id": pid,
            }
        )
    )
