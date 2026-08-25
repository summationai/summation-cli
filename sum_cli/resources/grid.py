"""`sumcli grid ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sum_cli.output import emit, ok
from sum_cli.commands import (
    ProfileOption,
    api_client,
    invalid_request,
    load_json_array,
    unwrap_data,
)
from sum_cli.stream_options import (
    FollowOption,
    WaitOption,
    post_with_wait_follow,
)

app = typer.Typer(no_args_is_help=True)

# Column types POST /v1/grid/tables accepts for kind=data. Kept here so --help and the
# client-side check name the same set the API's own enum does; a wrong type is a 422 that
# costs a round trip and reads as an upstream failure.
_COLUMN_TYPES = (
    "string",
    "integer",
    "decimal",
    "big_decimal",
    "boolean",
    "date",
    "datetime",
    "json",
    "uuid",
)

# The row store owns these names: the platform adds an integer s_id primary key and its own
# _sm_* bookkeeping columns when the table is built. Declaring one is a 422 upstream.
_RESERVED_COLUMN_NAMES = frozenset(
    {"s_id", "_sm_created_at", "_sm_event_at", "_sm_deleted", "_sm_branch", "_sm_event_by"}
)

# One column is one schema-change plan upstream, applied in order. The API caps a create at 50.
_MAX_COLUMNS = 50

_COLUMN_SHAPE_HINT = '[{"name": "event_id", "type": "uuid", "nullable": false}]'


def _parse_column_spec(spec: str) -> dict:
    """One ``--column name:type[:null|notnull]`` token as a request column."""
    parts = spec.split(":")
    if len(parts) not in (2, 3):
        invalid_request(
            f"--column {spec!r} is not name:type[:null|notnull].",
            "Use --column event_id:uuid or --column event_id:uuid:notnull.",
        )
    name = parts[0].strip()
    col_type = parts[1].strip().lower()
    if not name:
        invalid_request(f"--column {spec!r} has an empty name.", "Use --column <name>:<type>.")
    if col_type not in _COLUMN_TYPES:
        invalid_request(
            f"--column {spec!r} has unknown type {col_type!r}.",
            f"Use one of: {', '.join(_COLUMN_TYPES)}.",
        )
    nullable = True
    if len(parts) == 3:
        flag = parts[2].strip().lower()
        if flag not in ("null", "notnull"):
            invalid_request(
                f"--column {spec!r} has unknown nullability {parts[2]!r}.",
                "Use :null (the default) or :notnull.",
            )
        nullable = flag == "null"
    return {"name": name, "type": col_type, "nullable": nullable}


def _validate_columns(columns: list[dict]) -> None:
    """Refuse locally what the API refuses, so a bad schema costs no round trip."""
    if len(columns) > _MAX_COLUMNS:
        invalid_request(
            f"{len(columns)} columns exceeds the {_MAX_COLUMNS}-column limit for one create.",
            f"Declare at most {_MAX_COLUMNS} columns, then add more with the grid UI.",
        )
    seen: set[str] = set()
    for col in columns:
        lowered = col["name"].lower()
        if lowered in _RESERVED_COLUMN_NAMES:
            invalid_request(
                f"Column {col['name']!r} belongs to the table's row store and cannot be declared.",
                "Every data table already has an integer s_id primary key and a "
                "created-at timestamp.",
            )
        if lowered in seen:
            invalid_request(
                f"Duplicate column name: {col['name']}.",
                "Column names must be unique within the table, ignoring case.",
            )
        seen.add(lowered)


def _resolve_key_columns(key_column: list[str], columns: list[dict]) -> list[str]:
    """Rewrite each key to its column's own spelling; the API matches keys case-sensitively."""
    declared = {col["name"].lower(): col["name"] for col in columns}
    resolved: list[str] = []
    for key in key_column:
        canonical = declared.get(key.strip().lower())
        if canonical is None:
            invalid_request(
                f"--key-column {key!r} is not a declared column.",
                f"Name one of: {', '.join(col['name'] for col in columns)}.",
            )
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


def _load_columns_file(path: Path) -> list[dict]:
    """Read ``--columns-file`` as a JSON array of column objects."""
    parsed = load_json_array(path, "--columns-file", shape_hint=_COLUMN_SHAPE_HINT)
    columns: list[dict] = []
    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            invalid_request(
                f"--columns-file entry {index} is not a JSON object.",
                f"Use objects, e.g. {_COLUMN_SHAPE_HINT}.",
            )
        unknown = set(entry) - {"name", "type", "nullable"}
        if unknown:
            invalid_request(
                f"--columns-file entry {index} has unknown keys: {', '.join(sorted(unknown))}.",
                "Each column takes name, type, and optional nullable only.",
            )
        name = entry.get("name")
        col_type = entry.get("type")
        if not isinstance(name, str) or not name.strip():
            invalid_request(
                f"--columns-file entry {index} needs a non-empty string name.",
                f"Use objects, e.g. {_COLUMN_SHAPE_HINT}.",
            )
        if not isinstance(col_type, str) or col_type.lower() not in _COLUMN_TYPES:
            invalid_request(
                f"--columns-file entry {index} has unknown type {col_type!r}.",
                f"Use one of: {', '.join(_COLUMN_TYPES)}.",
            )
        nullable = entry.get("nullable", True)
        if not isinstance(nullable, bool):
            invalid_request(
                f"--columns-file entry {index} has a non-boolean nullable.",
                "Use true or false.",
            )
        columns.append({"name": name.strip(), "type": col_type.lower(), "nullable": nullable})
    return columns


def _build_create_payload(
    *,
    name: str,
    kind: str,
    query: str | None,
    column: list[str],
    columns_file: Path | None,
    key_column: list[str],
) -> dict:
    """The POST body for one kind, refusing the other kind's flags as the API does.

    Rejecting the unused flag rather than ignoring it is what makes a wrong guess
    self-correcting: --query with --kind data says so instead of quietly creating an
    empty table.
    """
    normalized_kind = kind.strip().lower()
    if normalized_kind not in ("calc", "data"):
        invalid_request(
            f"--kind {kind!r} is not calc or data.",
            "Use --kind calc for a query-defined table or --kind data for an appendable one.",
        )

    if normalized_kind == "calc":
        if not query:
            invalid_request(
                "--query is required with --kind calc.",
                'Pass --query "SELECT 1 AS new_total", or use --kind data with columns.',
            )
        if column or columns_file:
            invalid_request(
                "--column/--columns-file are not allowed with --kind calc.",
                "A calc table is defined by --query. Use --kind data to declare columns.",
            )
        if key_column:
            invalid_request(
                "--key-column is not allowed with --kind calc.",
                "Key columns apply to --kind data tables only.",
            )
        return {"name": name, "kind": "calc", "query": query}

    if query:
        invalid_request(
            "--query is not allowed with --kind data.",
            "A data table is defined by columns. Drop --query, or use --kind calc.",
        )
    if column and columns_file:
        invalid_request(
            "Pass either --column or --columns-file, not both.",
            "Use repeated --column for a short schema, or --columns-file for a longer one.",
        )
    if columns_file is not None:
        columns = _load_columns_file(columns_file)
    else:
        columns = [_parse_column_spec(spec) for spec in column]
    if not columns:
        invalid_request(
            "--kind data needs at least one column.",
            "Pass --column name:type (repeatable) or --columns-file <path>.",
        )
    _validate_columns(columns)

    payload = {"name": name, "kind": "data", "columns": columns}
    resolved_keys = _resolve_key_columns(key_column, columns)
    if resolved_keys:
        payload["key_columns"] = resolved_keys
    return payload


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
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help=(
                "calc (default) builds a calculation table from --query; "
                "data builds an empty appendable table from columns."
            ),
        ),
    ] = "calc",
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            help="SELECT query defining a calc table. Required for --kind calc.",
        ),
    ] = None,
    column: Annotated[
        list[str] | None,
        typer.Option(
            "--column",
            help=(
                "Data-table column as name:type[:null|notnull], repeatable and order-significant. "
                f"Types: {', '.join(_COLUMN_TYPES)}. Nullable unless :notnull."
            ),
        ),
    ] = None,
    columns_file: Annotated[
        Path | None,
        typer.Option(
            "--columns-file",
            help='JSON array, e.g. [{"name":"id","type":"uuid","nullable":false}].',
        ),
    ] = None,
    key_column: Annotated[
        list[str] | None,
        typer.Option(
            "--key-column",
            help="Business-key column matched on upsert, repeatable. Must name a declared column.",
        ),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    payload = _build_create_payload(
        name=name,
        kind=kind,
        query=query,
        column=column or [],
        columns_file=columns_file,
        key_column=key_column or [],
    )
    with api_client(ctx, profile) as c:
        body = c.request("POST", "/v1/grid/tables", json=payload)
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
