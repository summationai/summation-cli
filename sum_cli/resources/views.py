"""`sumcli views ...`"""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.output import emit, ok, truncate_list
from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    require_confirm,
    unwrap_data,
)

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_views(
    ctx: typer.Context,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/views")
    data = unwrap_data(body or {}, "data")
    items = data if isinstance(data, list) else []
    if not isinstance(items, list):
        items = []
    listed = truncate_list(items, count=count)
    emit(ok({"views": listed["items"], **{k: v for k, v in listed.items() if k != "items"}}))


@app.command("show")
def show_view(
    ctx: typer.Context,
    view_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/views/{view_id}")
    emit(ok({"view": unwrap_data(body or {}, "data") or body}))


@app.command("data")
def view_data(
    ctx: typer.Context,
    view_id: Annotated[str, typer.Argument()],
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    profile: ProfileOption = None,
) -> None:
    params = {"limit": limit} if limit else None
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/views/{view_id}/data", params=params)
    emit(ok({"data": unwrap_data(body or {}, "data") or body}))


@app.command("catalog-show")
def catalog_show(
    ctx: typer.Context,
    view_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/views/{view_id}/catalog")
    emit(ok({"catalog": unwrap_data(body or {}, "data") or body}))


@app.command("catalog-update")
def catalog_update(
    ctx: typer.Context,
    view_id: Annotated[str, typer.Argument()],
    description: Annotated[str | None, typer.Option("--description")] = None,
    profile: ProfileOption = None,
) -> None:
    payload: dict = {}
    if description is not None:
        payload["description"] = description
    with api_client(ctx, profile) as c:
        body = c.request("PATCH", f"/v1/views/{view_id}/catalog", json=payload)
    emit(ok({"catalog": unwrap_data(body or {}, "data") or body}))


@app.command("delete")
def delete_view(
    ctx: typer.Context,
    view_id: Annotated[str, typer.Argument()],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="views delete")
    with api_client(ctx, profile) as c:
        c.request("DELETE", f"/v1/views/{view_id}", params=api_confirm_params())
    emit(ok({"deleted": view_id}))
