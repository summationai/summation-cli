"""`sumcli connections ...`"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from sum_cli.output import emit, emit_error, err, ok, truncate_list
from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    require_confirm,
    unwrap_data,
)

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_connections(
    ctx: typer.Context,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/connections/data")
    data = unwrap_data(body or {}, "data")
    items = (
        data
        if isinstance(data, list)
        else (data.get("connections", []) if isinstance(data, dict) else [])
    )
    if not isinstance(items, list):
        items = []
    listed = truncate_list(items, count=count)
    emit(ok({"connections": listed["items"], **{k: v for k, v in listed.items() if k != "items"}}))


@app.command("show")
def show_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/connections/data/{connection_id}")
    emit(ok({"connection": unwrap_data(body or {}, "data") or body}))


@app.command("create")
def create_connection(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name")],
    type: Annotated[str, typer.Option("--type")],
    config_file: Annotated[
        Path | None, typer.Option("--config-file", help="JSON config+secrets.")
    ] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    profile: ProfileOption = None,
) -> None:
    payload: dict = {"name": name, "type": type}
    if description:
        payload["description"] = description
    if config_file:
        try:
            extra = json.loads(config_file.read_text())
        except json.JSONDecodeError as exc:
            emit_error(
                err(
                    "INVALID_REQUEST",
                    f"Invalid JSON in --config-file: {exc}",
                    "Provide a valid JSON file with config and secrets.",
                )
            )
        if not isinstance(extra, dict):
            emit_error(
                err(
                    "INVALID_REQUEST",
                    "--config-file must contain a JSON object.",
                    'Use {"config": {...}, "secrets": {...}}.',
                )
            )
        payload.setdefault("config", extra.get("config", {}))
        payload.setdefault("secrets", extra.get("secrets", {}))
    with api_client(ctx, profile) as c:
        body = c.request("POST", "/v1/connections/data", json=payload)
    emit(ok({"connection": unwrap_data(body or {}, "data") or body}))


@app.command("update")
def update_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument()],
    name: Annotated[str | None, typer.Option("--name")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    profile: ProfileOption = None,
) -> None:
    payload: dict = {}
    if name:
        payload["name"] = name
    if description:
        payload["description"] = description
    with api_client(ctx, profile) as c:
        body = c.request("PATCH", f"/v1/connections/data/{connection_id}", json=payload)
    emit(ok({"connection": unwrap_data(body or {}, "data") or body}))


@app.command("delete")
def delete_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument()],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="connections delete")
    with api_client(ctx, profile) as c:
        c.request(
            "DELETE",
            f"/v1/connections/data/{connection_id}",
            params=api_confirm_params(),
        )
    emit(ok({"deleted": connection_id}))


@app.command("test")
def test_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/connections/data/{connection_id}/tests")
    emit(ok({"test": unwrap_data(body or {}, "data") or body}))


@app.command("browse")
def browse_connection(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument()],
    path_prefix: Annotated[str | None, typer.Option("--path-prefix")] = None,
    max_results: Annotated[int, typer.Option("--max-results")] = 500,
    profile: ProfileOption = None,
) -> None:
    payload: dict = {"max_results": max_results}
    if path_prefix:
        payload["path_prefix"] = path_prefix
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/connections/data/{connection_id}/resources",
            json=payload,
        )
    emit(ok({"resources": unwrap_data(body or {}, "data") or body}))
