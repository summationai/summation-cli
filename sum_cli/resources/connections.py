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
from sum_cli.output import emit, ok, truncate_list
from sum_cli.output import invalid_request as _invalid

app = typer.Typer(no_args_is_help=True)

# ConnectionDatasetsAttachRequest.datasets maxItems; checked client-side so an
# over-long batch fails before the request (matches schedules._MAX_RECIPIENTS).
_MAX_ATTACH_DATASETS = 100
# GET /v1/connections/data/{id}/snapshots caps ``limit`` at 50 (ge=1, le=50).
_MAX_SNAPSHOT_LIMIT = 50
# Body keys ConnectionWriteRequest accepts from --config-file. ``name``/``type``/
# ``description`` are excluded: they come from flags, so a file key would fight the
# command line. Anything else is rejected rather than dropped -- a silently ignored
# snapshot_config would leave snapshotting off with exit 0.
_CONFIG_FILE_KEYS = ("config", "secrets", "snapshot_config")


def _load_json_object(path: Path, flag: str, *, shape_hint: str) -> dict:
    """Read a JSON object from ``path``, reporting every failure as INVALID_REQUEST.

    ``shape_hint`` is an example of the expected object, shown when the file parses
    but is not an object — each flag expects a different shape.
    """
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
        _invalid(f"{flag} must contain a JSON object.", f"Use an object, e.g. {shape_hint}.")
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
        Path | None,
        typer.Option(
            "--config-file",
            help="JSON object with any of: config, secrets, snapshot_config. "
            "Unknown top-level keys are rejected.",
        ),
    ] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    profile: ProfileOption = None,
) -> None:
    payload: dict = {"name": name, "type": type}
    if description:
        payload["description"] = description
    if config_file:
        extra = _load_json_object(
            config_file,
            "--config-file",
            shape_hint='{"config": {...}, "secrets": {...}}',
        )
        unknown = sorted(set(extra) - set(_CONFIG_FILE_KEYS))
        if unknown:
            _invalid(
                f"--config-file has unsupported top-level keys: {', '.join(unknown)}.",
                f"Use only {', '.join(_CONFIG_FILE_KEYS)}; set name, type, and "
                "description with their flags.",
            )
        # Assign, never setdefault: the file is the caller's explicit input and must
        # win over anything already on the payload.
        payload["config"] = extra.get("config", {})
        payload["secrets"] = extra.get("secrets", {})
        if "snapshot_config" in extra:
            # Only when present: the spec treats an omitted snapshot_config as "leave
            # the policy unchanged", so an empty default would be a real instruction.
            payload["snapshot_config"] = extra["snapshot_config"]
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
            "Omit to inherit the connection setting. With --datasets-file, this "
            "overrides snapshot_enabled on every entry.",
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
    if datasets_file and (name or description):
        # The file names each dataset itself, so these flags would be silently
        # dropped. Reject rather than ignore.
        _invalid(
            "--name/--description cannot be combined with --datasets-file.",
            'Set "name" and "description" on each entry inside --datasets-file.',
        )
    if datasets_file:
        parsed = _load_json_object(
            datasets_file,
            "--datasets-file",
            shape_hint='{"datasets": [{"from_source": "db.schema.table"}]}',
        )
        specs = parsed.get("datasets")
        if not isinstance(specs, list) or not specs:
            _invalid(
                "--datasets-file must contain a non-empty `datasets` array.",
                'Use {"datasets": [{"from_source": "db.schema.table"}]}.',
            )
        # Element types matter: the snapshot loop below assigns into each entry, so a
        # bare string would raise TypeError instead of reporting a usable error.
        if not all(isinstance(spec, dict) for spec in specs):
            _invalid(
                "--datasets-file `datasets` entries must be JSON objects.",
                'Use {"datasets": [{"from_source": "db.schema.table"}]}, not a list of strings.',
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


# ---------------------------------------------------------------------------
# App connections — /v1/connections/app
#
# A separate resource from the data connections above: third-party apps
# (NetSuite, SharePoint, Salesforce ...) whose tools the agent can call during
# chat, rather than sources you query. sum-api tags them "App Connectors" vs
# "Data Connectors" under a shared URL prefix, so they live in this group with
# an ``app-`` prefix — the bare verbs belong to the data family, and the ``apps``
# resource name is reserved for SumApps (/v1/sum-apps).
#
# Two nouns, per the API's own vocabulary:
#   * app connector — a connectable type in the catalog (key, no id)
#   * app connection — an instance the caller has connected (id, status)
# ---------------------------------------------------------------------------


def _emit_app_connection(body: object, **extra: object) -> None:
    emit(ok({"connection": unwrap_data(body or {}, "data") or body, **extra}))


def _set_app_chat_enabled(
    ctx: typer.Context, connection_id: str, profile: str | None, *, enabled: bool
) -> None:
    """PATCH the connection's only writable field (``enabled_for_chat``)."""
    with api_client(ctx, profile) as c:
        body = c.request(
            "PATCH",
            f"/v1/connections/app/{connection_id}",
            json={"enabled_for_chat": enabled},
        )
    _emit_app_connection(body, enabled_for_chat=enabled)


@app.command("app-catalog")
def app_catalog(
    ctx: typer.Context,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/connections/app/catalog")
    data = unwrap_data(body or {}, "data")
    listed = truncate_list(extract_list(data, "apps"), count=count)
    emit(ok({"apps": listed["items"], **{k: v for k, v in listed.items() if k != "items"}}))


@app.command("app-tools")
def app_tools(
    ctx: typer.Context,
    app_key: Annotated[
        str,
        typer.Argument(
            help="Catalog app key as returned by `connections app-catalog`, e.g. netsuite."
        ),
    ],
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/connections/app/catalog/{app_key}/tools")
    data = unwrap_data(body or {}, "data")
    listed = truncate_list(extract_list(data, "tools"), count=count)
    emit(
        ok(
            {
                "tools": listed["items"],
                **{k: v for k, v in listed.items() if k != "items"},
                "app_key": app_key,
            }
        )
    )


@app.command("app-list")
def app_list(
    ctx: typer.Context,
    enabled_for_chat_only: Annotated[
        bool,
        typer.Option(
            "--enabled-for-chat-only",
            help="Return only connections the agent may use during chat.",
        ),
    ] = False,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    params: dict = {}
    # Send only when set: the server already defaults to false, and an explicit
    # false would pin the default if it ever changes.
    if enabled_for_chat_only:
        params["enabled_for_chat_only"] = True
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/connections/app", params=params)
    data = unwrap_data(body or {}, "data")
    listed = truncate_list(extract_list(data, "connections"), count=count)
    emit(ok({"connections": listed["items"], **{k: v for k, v in listed.items() if k != "items"}}))


@app.command("app-show")
def app_show(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="App connection id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/connections/app/{connection_id}")
    _emit_app_connection(body)


@app.command("app-enable-chat")
def app_enable_chat(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="App connection id.")],
    profile: ProfileOption = None,
) -> None:
    _set_app_chat_enabled(ctx, connection_id, profile, enabled=True)


@app.command("app-disable-chat")
def app_disable_chat(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="App connection id.")],
    profile: ProfileOption = None,
) -> None:
    _set_app_chat_enabled(ctx, connection_id, profile, enabled=False)


@app.command("app-disconnect")
def app_disconnect(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="App connection id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/connections/app/{connection_id}/disconnect")
    emit(
        ok(
            {
                "disconnected": connection_id,
                "result": unwrap_data(body or {}, "data") or body,
            }
        )
    )


@app.command("app-delete")
def app_delete(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="App connection id.")],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="connections app-delete")
    with api_client(ctx, profile) as c:
        c.request(
            "DELETE",
            f"/v1/connections/app/{connection_id}",
            params=api_confirm_params(),
        )
    emit(ok({"deleted": connection_id}))
