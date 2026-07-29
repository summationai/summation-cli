"""`sumcli queries ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from sum_cli.output import emit, emit_error, err, ok
from sum_cli.commands import ProfileOption, api_client, unwrap_data

app = typer.Typer(no_args_is_help=True)

# sum-api QueryExecutionRequest.limit: default 100, maximum 10000.
_API_DEFAULT_LIMIT = 100
_API_MAX_PAGE = 10000


def _strip_sql(sql: str) -> str:
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _page_sql(sql: str, offset: int) -> str:
    """Wrap user SQL so a subsequent page can apply OFFSET under the API row cap."""
    stripped = _strip_sql(sql)
    if offset <= 0:
        return stripped
    return f"SELECT * FROM (\n{stripped}\n) AS _sumcli_page\nOFFSET {int(offset)}"


def extract_query_rows(data: object) -> list[Any]:
    """Pull row list from query-execution payloads (nested or flat)."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict):
        nested = result.get("rows")
        if isinstance(nested, list):
            return nested
    for key in ("rows", "results"):
        rows = data.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _execute_query(client: Any, sql: str, page_limit: int) -> dict[str, Any]:
    body = client.request(
        "POST",
        "/v1/query-executions",
        json={"sql": sql, "limit": page_limit},
    )
    data = unwrap_data(body or {}, "data") or body
    return data if isinstance(data, dict) else {"raw": data}


def _run_paginated(client: Any, query_sql: str, desired: int) -> dict[str, Any]:
    """Fetch up to `desired` rows, paging when desired exceeds the API per-request max."""
    pages: list[dict[str, Any]] = []
    rows: list[Any] = []
    offset = 0
    exhausted = False

    while len(rows) < desired:
        page_limit = min(_API_MAX_PAGE, desired - len(rows))
        page_sql = _page_sql(query_sql, offset) if offset > 0 else _strip_sql(query_sql)
        data = _execute_query(client, page_sql, page_limit)
        pages.append(data)
        page_rows = extract_query_rows(data)
        rows.extend(page_rows)
        if len(page_rows) < page_limit:
            exhausted = True
            break
        offset += len(page_rows)
        if desired <= _API_MAX_PAGE:
            break  # single request satisfied the ask

    clipped = rows[:desired]
    # Truncated when we hit --limit on a full final page (more rows may exist).
    truncated = len(clipped) >= desired and not exhausted

    result: dict[str, Any] = {
        "query": pages[-1] if len(pages) == 1 else {"status": "succeeded", "pages": len(pages)},
        "rows": clipped,
        "showing": len(clipped),
        "limit": desired,
        "truncated": truncated,
    }
    if len(pages) > 1:
        result["pages"] = len(pages)
    return result


@app.command("run")
def run_query(
    ctx: typer.Context,
    sql: Annotated[str | None, typer.Option("--sql")] = None,
    file: Annotated[Path | None, typer.Option("--file")] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help=(
                "Max rows to return (API default 100, max 10000 per request). "
                "Values above 10000 auto-paginate with OFFSET."
            ),
            min=1,
        ),
    ] = None,
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

    desired = limit if limit is not None else _API_DEFAULT_LIMIT
    with api_client(ctx, profile) as c:
        emit(ok(_run_paginated(c, query_sql, desired)))
