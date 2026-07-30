"""`sumcli queries ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from sum_cli.client import ApiError
from sum_cli.commands import ProfileOption, api_client, unwrap_data
from sum_cli.output import emit, emit_error, err, ok

app = typer.Typer(no_args_is_help=True)

# sum-api QueryExecutionRequest.limit: default 100, maximum 10000.
_API_DEFAULT_LIMIT = 100
_API_MAX_PAGE = 10000
_QUERY_FAILED_STATES = frozenset({"FAILED", "ERROR"})


def _strip_sql(sql: str) -> str:
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _page_sql(sql: str, offset: int, page_limit: int) -> str:
    """Wrap user SQL so a subsequent page can apply LIMIT/OFFSET under the API row cap."""
    stripped = _strip_sql(sql)
    if offset <= 0:
        return stripped
    return (
        f"SELECT * FROM (\n{stripped}\n) AS _sumcli_page\n"
        f"LIMIT {int(page_limit)} OFFSET {int(offset)}"
    )


def _extract_query_rows(data: object) -> list[Any]:
    """Pull row list from query-execution payloads (nested or flat dict)."""
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


def _page_failed(data: object) -> bool:
    """Defense-in-depth: real API failures usually arrive as ApiError (non-200)."""
    if not isinstance(data, dict):
        return False
    status = str(data.get("status", "")).upper()
    return status in _QUERY_FAILED_STATES


def _api_error_detail(exc: ApiError) -> str:
    """Best-effort message from a problem+json (or opaque) ApiError body."""
    body = exc.body
    if isinstance(body, dict):
        err_obj = body.get("error") or body
        if isinstance(err_obj, dict):
            for key in ("message", "detail", "title"):
                value = err_obj.get(key)
                if value:
                    return str(value)
        for key in ("message", "detail", "title"):
            value = body.get(key)
            if value:
                return str(value)
    return str(body)


def _emit_query_failed(
    data: dict[str, Any],
    *,
    page: int,
    offset: int,
    rows_so_far: int,
) -> NoReturn:
    detail = data.get("error")
    status = data.get("status")
    if detail:
        message = f"Query execution failed on page {page} ({detail})."
    else:
        message = f"Query execution failed on page {page} with status {status!r}."
    message += f" Retrieved {rows_so_far} row(s) before the failure."
    emit_error(
        err(
            "QUERY_FAILED",
            message,
            "Fix the query and retry. When using --limit above 10000, include "
            "ORDER BY so OFFSET pages are stable.",
            data={
                "page": page,
                "offset": offset,
                "rows_so_far": rows_so_far,
                "query": data,
            },
        )
    )


def _execute_query(client: Any, sql: str, page_limit: int) -> dict[str, Any]:
    body = client.request(
        "POST",
        "/v1/query-executions",
        json={"sql": sql, "limit": page_limit},
    )
    data = unwrap_data(body or {}, "data") or body
    if isinstance(data, dict):
        return data
    # Flat list payloads are rare; normalize so _extract_query_rows can read them.
    if isinstance(data, list):
        return {"rows": data}
    return {"raw": data}


def _run_paginated(client: Any, query_sql: str, desired: int) -> dict[str, Any]:
    """Fetch up to `desired` rows, paging when desired exceeds the API per-request max."""
    pages: list[dict[str, Any]] = []
    rows: list[Any] = []
    offset = 0
    exhausted = False

    while len(rows) < desired:
        page_limit = min(_API_MAX_PAGE, desired - len(rows))
        page_sql = (
            _page_sql(query_sql, offset, page_limit) if offset > 0 else _strip_sql(query_sql)
        )
        try:
            data = _execute_query(client, page_sql, page_limit)
        except ApiError as exc:
            # Live API: query failures are non-200 problem+json → ApiError here,
            # not a 200 body with status=failed. Re-emit with pagination context.
            _emit_query_failed(
                {
                    "status": "failed",
                    "error": _api_error_detail(exc),
                    "http_status": exc.status,
                    "body": exc.body,
                },
                page=len(pages) + 1,
                offset=offset,
                rows_so_far=len(rows),
            )
        if _page_failed(data):
            _emit_query_failed(
                data,
                page=len(pages) + 1,
                offset=offset,
                rows_so_far=len(rows),
            )
        pages.append(data)
        page_rows = _extract_query_rows(data)
        rows.extend(page_rows)
        if len(page_rows) < page_limit:
            exhausted = True
            break
        offset += len(page_rows)
        if desired <= _API_MAX_PAGE:
            break  # single request satisfied the ask

    clipped = rows[:desired]
    # truncated=true means we hit --limit on a full final page (more rows may exist).
    truncated = len(clipped) >= desired and not exhausted

    return {
        "query": pages[-1],
        "rows": clipped,
        "showing": len(clipped),
        "limit": desired,
        "truncated": truncated,
        "pages": len(pages),
    }


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
                "Values above 10000 auto-paginate with LIMIT/OFFSET — include ORDER BY "
                "for stable pages. truncated=true means the cap was hit and more may exist."
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
