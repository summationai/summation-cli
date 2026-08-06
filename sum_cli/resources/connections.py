"""`sumcli connections ...`"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    extract_list,
    require_confirm,
    unwrap_data,
)
from sum_cli.output import emit, emit_error, err, ok, truncate_list

app = typer.Typer(no_args_is_help=True)

# ConnectionDatasetsAttachRequest.datasets maxItems; checked client-side so an
# over-long batch fails before the request (matches schedules._MAX_RECIPIENTS).
_MAX_ATTACH_DATASETS = 100
# GET /v1/connections/data/{id}/snapshots caps ``limit`` at 50 (ge=1, le=50).
_MAX_SNAPSHOT_LIMIT = 50


def _invalid(message: str, fix: str) -> None:
    emit_error(err("INVALID_REQUEST", message, fix))


def _load_json_object(path: Path, flag: str) -> dict:
    try:
        parsed = json.loads(path.read_text())
    except UnicodeDecodeError as exc:
        # Not JSONDecodeError: read_text() fails before parsing on non-UTF-8 bytes.
        _invalid(f"{flag} is not valid UTF-8 text: {exc}", f"Save {flag} as UTF-8 encoded JSON.")
    except ValueError as exc:
        # Subsumes json.JSONDecodeError.
        _invalid(f"Invalid JSON in {flag}: {exc}", f"Provide a valid JSON object in {flag}.")
    except OSError as exc:
        _invalid(
            f"Cannot read {flag}: {exc}", f"Check that the {flag} path exists and is readable."
        )
    if not isinstance(parsed, dict):
        _invalid(
            f"{flag} must contain a JSON object.",
            'Use an object, e.g. {"datasets": [{"from_source": "db.schema.table"}]}.',
        )
    return parsed


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


@app.command("datasets")
def list_datasets(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Connection id.")],
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/connections/data/{connection_id}/datasets")
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "datasets")
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "datasets": listed["items"],
                **{k: v for k, v in listed.items() if k != "items"},
                "connection_id": connection_id,
            }
        )
    )


@app.command("attach-datasets")
def attach_datasets(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Connection id.")],
    from_source: Annotated[
        list[str] | None,
        typer.Option(
            "--from-source",
            help="Source path exactly as returned by `connections browse`; repeat to attach "
            "several. Omit for request-shaped connections (HTTP APIs) and use --datasets-file.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Dataset name; only valid with a single --from-source. The server derives "
            "one when omitted.",
        ),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option("--description", help="Description; only valid with a single --from-source."),
    ] = None,
    snapshot_enabled: Annotated[
        bool | None,
        typer.Option(
            "--snapshot-enabled/--no-snapshot-enabled",
            help="Opt these datasets into snapshotting regardless of the connection's policy. "
            "Omit to inherit the connection setting.",
        ),
    ] = None,
    datasets_file: Annotated[
        Path | None,
        typer.Option(
            "--datasets-file",
            help='JSON file for full control: {"datasets": [{"from_source": ..., "params": '
            "{...}}]}. Required for connector-specific params. Cannot combine with "
            "--from-source.",
        ),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    if datasets_file and from_source:
        _invalid(
            "Use either --from-source or --datasets-file, not both.",
            "Put every dataset in --datasets-file, or drop it and repeat --from-source.",
        )
    if datasets_file:
        parsed = _load_json_object(datasets_file, "--datasets-file")
        specs = parsed.get("datasets")
        if not isinstance(specs, list) or not specs:
            _invalid(
                "--datasets-file must contain a non-empty `datasets` array.",
                'Use {"datasets": [{"from_source": "db.schema.table"}]}.',
            )
    else:
        if not from_source:
            _invalid(
                "No datasets given.",
                "Pass --from-source (repeatable) or --datasets-file.",
            )
        # --name/--description describe one dataset; applying either across a repeated
        # --from-source would silently collide, so require a single source for them.
        if len(from_source) > 1 and (name or description):
            _invalid(
                f"--name/--description cannot apply to {len(from_source)} sources.",
                "Attach one --from-source at a time, or use --datasets-file to name each.",
            )
        specs = []
        for source in from_source:
            spec: dict = {"from_source": source}
            if name:
                spec["name"] = name
            if description:
                spec["description"] = description
            specs.append(spec)
    if snapshot_enabled is not None:
        # Flag applies to every spec; --datasets-file entries may set it per dataset,
        # and an explicit flag is the more specific instruction, so it wins.
        for spec in specs:
            spec["snapshot_enabled"] = snapshot_enabled
    if len(specs) > _MAX_ATTACH_DATASETS:
        _invalid(
            f"{len(specs)} datasets given; the limit is {_MAX_ATTACH_DATASETS} per request.",
            f"Attach {_MAX_ATTACH_DATASETS} or fewer at a time.",
        )
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/connections/data/{connection_id}/datasets",
            json={"datasets": specs},
        )
    data = unwrap_data(body or {}, "data")
    emit(
        ok(
            {
                "datasets": extract_list(data, "datasets"),
                "connection_id": connection_id,
            }
        )
    )


@app.command("snapshot")
def snapshot_dataset(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Connection id.")],
    dataset_id: Annotated[str, typer.Argument(help="Dataset id on this connection.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/connections/data/{connection_id}/datasets/{dataset_id}/snapshots",
        )
    emit(
        ok(
            {
                "snapshot": unwrap_data(body or {}, "data") or body,
                "connection_id": connection_id,
                "dataset_id": dataset_id,
            }
        )
    )


@app.command("snapshots")
def list_snapshots(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Connection id.")],
    limit: Annotated[
        int,
        typer.Option("--limit", help=f"Most recent runs to return (1-{_MAX_SNAPSHOT_LIMIT})."),
    ] = 10,
    profile: ProfileOption = None,
) -> None:
    # Client-side so an out-of-range value reports a fixable message instead of a 422.
    if not 1 <= limit <= _MAX_SNAPSHOT_LIMIT:
        _invalid(
            f"--limit must be between 1 and {_MAX_SNAPSHOT_LIMIT}; got {limit}.",
            f"Pass --limit between 1 and {_MAX_SNAPSHOT_LIMIT}.",
        )
    with api_client(ctx, profile) as c:
        body = c.request(
            "GET",
            f"/v1/connections/data/{connection_id}/snapshots",
            params={"limit": limit},
        )
    data = unwrap_data(body or {}, "data")
    emit(
        ok(
            {
                "runs": extract_list(data, "runs", "snapshots"),
                "connection_id": connection_id,
            }
        )
    )
