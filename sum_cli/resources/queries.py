"""`sumcli queries ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sum_cli.output import emit, emit_error, err, ok, truncate_list
from sum_cli.commands import ProfileOption, api_client, unwrap_data

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run_query(
    ctx: typer.Context,
    sql: Annotated[str | None, typer.Option("--sql")] = None,
    file: Annotated[Path | None, typer.Option("--file")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    profile: ProfileOption = None,
) -> None:
    if file:
        query_sql = file.read_text()
    elif sql:
        query_sql = sql
    else:
        emit_error(
            err("INVALID_REQUEST", "Provide --sql or --file.", "Pass SQL inline or from a file.")
        )
    payload: dict = {"sql": query_sql}
    if limit is not None:
        payload["limit"] = limit
    with api_client(ctx, profile) as c:
        body = c.request("POST", "/v1/query-executions", json=payload)
    data = unwrap_data(body or {}, "data") or body
    rows = []
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("results") or []
    if isinstance(rows, list):
        listed = truncate_list(rows, default_limit=limit or 100)
        emit(
            ok(
                {
                    "query": data,
                    "rows": listed.get("items", rows),
                    "truncated": listed.get("truncated", False),
                }
            )
        )
    else:
        emit(ok({"query": data}))
