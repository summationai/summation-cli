"""`sumcli tables ...`"""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Annotated

import typer

from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    require_confirm,
    require_project,
    unwrap_data,
)
from sum_cli.output import emit, emit_error, err, ndjson, ok, truncate_list
from sum_cli.streaming import exit_if_stream_failed
from sum_cli.tempfiles import write_temp_bytes

app = typer.Typer(no_args_is_help=True)

_IMPORT_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "SUCCEEDED", "SUCCESS", "ERROR"})
_IMPORT_FAILED_STATES = frozenset({"FAILED", "ERROR"})


def _resolve_table_id(c, table_name: str) -> str | None:
    """Look up a table's id by name.

    ``/v1/table-imports`` returns the import status (request id, asset id, status)
    but never the resulting table id, so we resolve it from ``/v1/tables`` after a
    successful import to save the caller a separate list-and-match step.
    """
    body = c.request("GET", "/v1/tables")
    data = unwrap_data(body or {}, "data")
    tables = (
        data.get("tables", [])
        if isinstance(data, dict)
        else (data if isinstance(data, list) else [])
    )
    for table in tables:
        if isinstance(table, dict) and table.get("tableName") == table_name:
            return table.get("id")
    return None


def _emit_import_wait_terminal(terminal: dict) -> None:
    if terminal.get("ok") is False:
        ndjson("error", **terminal)
    else:
        ndjson("result", **terminal)
    exit_if_stream_failed(terminal)


def _load_rows_from_flags(
    *,
    rows: str | None,
    file: Path | None,
) -> list:
    if (rows is None) == (file is None):
        emit_error(
            err(
                "INVALID_FLAGS",
                "Provide exactly one of --rows or --file.",
                'Pass --rows \'[{"col": "val"}]\' or --file rows.json.',
            )
        )
    raw = file.read_text() if file is not None else rows
    try:
        parsed = json.loads(raw)  # type: ignore[arg-type]
    except json.JSONDecodeError as exc:
        emit_error(
            err("INVALID_JSON", f"Rows are not valid JSON: {exc}.", "Pass a JSON array of objects.")
        )
    if not isinstance(parsed, list):
        emit_error(
            err(
                "INVALID_ROWS",
                "Rows must be a JSON array of objects.",
                "Wrap your rows in [ ... ].",
            )
        )
    return parsed


def _emit_append_result(result: dict) -> None:
    status = str(result.get("status", "")).upper()
    if status == "FULL":
        emit(ok({"result": result}))
        return

    errors = result.get("errors") or []
    inserted = result.get("insertedRefIds") or []
    if status == "PARTIAL":
        emit_error(
            err(
                "APPEND_PARTIAL",
                f"Partial append: {len(inserted)} row(s) inserted, {len(errors)} failed.",
                "Review errors and retry only the failed rows.",
                data={"status": status, "inserted_ref_ids": inserted, "errors": errors},
            )
        )

    emit_error(
        err(
            "APPEND_FAILED",
            "Append did not complete successfully.",
            "Review the table schema and row values, then retry.",
            data={"status": status or None, "errors": errors},
        )
    )


def _emit_upsert_result(result: dict) -> None:
    errors = result.get("errors") or []
    inserted = result.get("inserted") or 0
    updated = result.get("updated") or 0
    if not errors:
        emit(ok({"result": result}))
        return
    if inserted or updated:
        emit_error(
            err(
                "UPSERT_PARTIAL",
                f"Partial upsert: {inserted} inserted, {updated} updated, {len(errors)} failed.",
                "Review errors and retry only the failed rows.",
                data={"inserted": inserted, "updated": updated, "errors": errors},
            )
        )
    emit_error(
        err(
            "UPSERT_FAILED",
            "Upsert did not complete successfully.",
            "Review the table schema and row values, then retry.",
            data={"inserted": inserted, "updated": updated, "errors": errors},
        )
    )


@app.command("list")
def list_tables(
    ctx: typer.Context,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/tables")
    data = unwrap_data(body or {}, "data")
    items = (
        data.get("tables", [])
        if isinstance(data, dict)
        else (data if isinstance(data, list) else [])
    )
    listed = truncate_list(items, count=count)
    emit(ok({"tables": listed["items"], **{k: v for k, v in listed.items() if k != "items"}}))


@app.command("show")
def show_table(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/tables/{table_id}")
    emit(ok({"table": unwrap_data(body or {}, "data") or body}))


@app.command("data")
def table_data(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    profile: ProfileOption = None,
) -> None:
    params = {"limit": limit} if limit else None
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/tables/{table_id}/data", params=params)
    emit(ok({"data": unwrap_data(body or {}, "data") or body}))


@app.command("catalog-show")
def catalog_show(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/tables/{table_id}/catalog")
    emit(ok({"catalog": unwrap_data(body or {}, "data") or body}))


@app.command("catalog-update")
def catalog_update(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    description: Annotated[str | None, typer.Option("--description")] = None,
    agent_description: Annotated[str | None, typer.Option("--agent-description")] = None,
    profile: ProfileOption = None,
) -> None:
    payload: dict = {}
    if description is not None:
        payload["description"] = description
    if agent_description is not None:
        payload["agent_description"] = agent_description
    with api_client(ctx, profile) as c:
        body = c.request("PATCH", f"/v1/tables/{table_id}/catalog", json=payload)
    emit(ok({"catalog": unwrap_data(body or {}, "data") or body}))


@app.command("delete")
def delete_table(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="tables delete")
    with api_client(ctx, profile) as c:
        c.request("DELETE", f"/v1/tables/{table_id}", params=api_confirm_params())
    emit(ok({"deleted": table_id}))


@app.command("append")
def append_rows(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    rows: Annotated[
        str | None,
        typer.Option("--rows", help="Rows as an inline JSON array of objects."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Path to a JSON file holding an array of row objects."),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    """Append rows to a table (append-only). Each row must include the table primary key (s_id)."""
    parsed = _load_rows_from_flags(rows=rows, file=file)
    payload: dict = {"rows": parsed}
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/tables/{table_id}/rows", json=payload)
    result = unwrap_data(body or {}, "data") or body
    if not isinstance(result, dict):
        emit(ok({"result": result}))
        return
    _emit_append_result(result)


@app.command("upsert")
def upsert_rows(
    ctx: typer.Context,
    table_id: Annotated[str, typer.Argument()],
    rows: Annotated[
        str | None,
        typer.Option("--rows", help="Rows as an inline JSON array of objects."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Path to a JSON file holding an array of row objects."),
    ] = None,
    key_column: Annotated[
        list[str] | None,
        typer.Option(
            "--key-column",
            help="Business-key column(s) for row identity; omit to use the table's declared keys.",
        ),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    """Upsert rows by business key (insert or update). Do not include s_id in rows."""
    parsed = _load_rows_from_flags(rows=rows, file=file)
    for index, row in enumerate(parsed):
        if not isinstance(row, dict):
            emit_error(
                err(
                    "INVALID_ROWS",
                    f"Row {index} is not a JSON object.",
                    'Pass an array of objects, e.g. [{"event_id": "..."}].',
                )
            )
        if "s_id" in row or "sId" in row:
            emit_error(
                err(
                    "INVALID_ROWS",
                    f"Row {index} must not include s_id.",
                    "Upsert derives s_id from business keys. Use tables append to supply s_id.",
                )
            )
    payload: dict = {"rows": parsed}
    if key_column:
        payload["key_columns"] = key_column
    with api_client(ctx, profile) as c:
        body = c.request("PUT", f"/v1/tables/{table_id}/rows", json=payload)
    result = unwrap_data(body or {}, "data") or body
    if not isinstance(result, dict):
        emit(ok({"result": result}))
        return
    _emit_upsert_result(result)


@app.command("import-status")
def import_status(
    ctx: typer.Context,
    import_id: Annotated[str, typer.Argument()],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/table-imports/{import_id}")
    emit(ok({"import": unwrap_data(body or {}, "data") or body}))


def _resolve_remote_path_to_file_id(c: "object", pid: str, remote_path: str) -> str:
    """Look up the file_id for a project-tree path. Errors on 0 or >1 matches."""
    body = c.request("GET", f"/v1/projects/{pid}/files", params={"q": ""})  # type: ignore[attr-defined]
    data = unwrap_data(body or {}, "data") or body
    entries = data.get("entries", []) if isinstance(data, dict) else []
    target = remote_path if remote_path.startswith("/") else "/" + remote_path
    matches = [
        f
        for f in entries
        if isinstance(f, dict) and (f.get("folderPath") or "") + (f.get("fileName") or "") == target
    ]
    if not matches:
        emit_error(
            err(
                "FILE_NOT_FOUND",
                f"No project file matches {remote_path!r}.",
                "Run `sumcli files list` to see available paths.",
            )
        )
    if len(matches) > 1:
        ids = ", ".join(m.get("id", "?") for m in matches)
        emit_error(
            err(
                "AMBIGUOUS_PATH",
                f"Multiple project files match {remote_path!r}: {ids}.",
                "Pass --file-id <id> to disambiguate.",
            )
        )
    return matches[0]["id"]


@app.command("import")
def import_table(
    ctx: typer.Context,
    table_name: Annotated[str, typer.Option("--table", help="Target table name.")],
    local: Annotated[
        bool, typer.Option("--local", help="Source is a local filesystem path.")
    ] = False,
    remote: Annotated[
        bool, typer.Option("--remote", help="Source is in the project file tree.")
    ] = False,
    path: Annotated[
        str | None,
        typer.Option(
            "--path",
            help="Local filesystem path (with --local) or project file path (with --remote).",
        ),
    ] = None,
    file_id: Annotated[
        str | None,
        typer.Option("--file-id", help="Remote project file_id (implies --remote)."),
    ] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    wait: Annotated[
        bool, typer.Option("--wait/--no-wait", help="Poll import until complete.")
    ] = True,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-import into an existing table, replacing its rows (sends confirm=true).",
        ),
    ] = False,
    separator: Annotated[str, typer.Option("--separator")] = ",",
    profile: ProfileOption = None,
) -> None:
    if file_id is not None:
        remote = True
    if local and remote:
        emit_error(
            err(
                "INVALID_FLAGS",
                "--local and --remote are mutually exclusive.",
                "Pick one source mode.",
            )
        )
    if not local and not remote:
        emit_error(
            err(
                "INVALID_FLAGS",
                "Specify --local or --remote.",
                "Use --local --path <file> for a laptop file, or --remote --path <project_path> / --file-id <id>.",
            )
        )
    if path is not None and file_id is not None:
        emit_error(
            err(
                "INVALID_FLAGS",
                "--path and --file-id are mutually exclusive.",
                "Pass only one.",
            )
        )
    if local and (path is None or file_id is not None):
        emit_error(
            err(
                "INVALID_FLAGS", "--local requires --path <file>.", "Pass --path /path/to/file.csv."
            )
        )
    if remote and path is None and file_id is None:
        emit_error(
            err(
                "INVALID_FLAGS",
                "--remote requires --path <project_path> or --file-id <id>.",
                "Pass one of them.",
            )
        )

    if local:
        file = Path(path)  # type: ignore[arg-type]
    else:
        pid = require_project(ctx, project)
        with api_client(ctx, profile) as c:
            resolved_file_id = file_id or _resolve_remote_path_to_file_id(c, pid, path)  # type: ignore[arg-type]
            meta = c.request("GET", f"/v1/projects/{pid}/files/{resolved_file_id}")
            meta_data = unwrap_data(meta or {}, "data") or meta or {}
            remote_path_str = meta_data.get("path") if isinstance(meta_data, dict) else None
            remote_filename = (remote_path_str or "").rsplit("/", 1)[-1] or (
                path.rsplit("/", 1)[-1] if path else resolved_file_id
            )
            raw = c.request_bytes("GET", f"/v1/projects/{pid}/files/{resolved_file_id}/content")
        file = write_temp_bytes(prefix="sumcli-tblimport-", suffix=f"-{remote_filename}", data=raw)

    filename = file.name
    with api_client(ctx, profile) as c:
        upload = c.request("POST", "/v1/assets/upload-urls", json={"filename": filename})
        upload_data = unwrap_data(upload or {}, "data") or upload
        if not isinstance(upload_data, dict):
            if wait:
                ndjson("step", name="upload-urls", status="failed")
                _emit_import_wait_terminal(
                    err(
                        "IMPORT_FAILED",
                        "Upload URL request returned an unexpected response.",
                        "Check the file path and API credentials, then retry.",
                    )
                )
            emit(ok({"step": "upload-urls", "raw": upload}))
            return
        asset_id = upload_data.get("assetId")
        upload_url = upload_data.get("uploadUrl")
        if not asset_id or not upload_url:
            if wait:
                ndjson("step", name="upload", status="failed")
                _emit_import_wait_terminal(
                    err(
                        "IMPORT_FAILED",
                        "Upload URL response missing assetId or uploadUrl.",
                        "Retry the import; if it persists, check sum-api asset upload status.",
                    )
                )
            emit(ok({"upload": upload_data}))
            return
        if wait:
            ndjson("step", name="upload", status="started")
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        # NOTE: This is the ONLY approved non-sum-api network call in the CLI.
        # sum-api hands back a pre-signed object-storage `uploadUrl` above, and we
        # PUT the file bytes directly to it. The upload target is dictated entirely
        # by sum-api (we never construct the URL ourselves), so the "CLI only talks
        # to sum-api" boundary still holds in spirit. See README "Network boundary".
        c.put_url(upload_url, file.read_bytes(), content_type=content_type)
        if wait:
            ndjson("step", name="upload", status="completed")
        preview = c.request(
            "POST",
            f"/v1/assets/{asset_id}/previews",
            json={
                "column_mappings": [],
                "csv": {
                    "char_encoding": "UTF_8",
                    "column_separator": separator,
                    "quote_character": '"',
                    "header_row": 1,
                    "first_data_row": 2,
                },
                "page_number": 1,
                "page_size": 50,
            },
        )
        preview_data = unwrap_data(preview or {}, "data") or preview
        # --refresh replaces the existing table's rows; the explicit flag is the
        # user's confirmation, so the CLI sends the API's required confirm=true.
        # Without it the import is NEW: the pipeline refuses a name collision.
        created = c.request(
            "POST",
            "/v1/table-imports",
            params={"confirm": "true"} if refresh else None,
            json={
                "asset_id": asset_id,
                "table_name": table_name,
                "import_type": "FULL_REFRESH" if refresh else "NEW",
                "column_mappings": [],
            },
        )
        created_data = unwrap_data(created or {}, "data") or created
        import_id = created_data.get("importRequestId") if isinstance(created_data, dict) else None
        final_import = created_data
        if wait and import_id:
            for _ in range(60):
                status_body = c.request("GET", f"/v1/table-imports/{import_id}")
                status_data = unwrap_data(status_body or {}, "data") or status_body
                state = status_data.get("importStatus") if isinstance(status_data, dict) else None
                ndjson("progress", name="import", message=str(state))
                if state in _IMPORT_TERMINAL_STATES:
                    final_import = status_data
                    break
                time.sleep(2)
            final_state = (
                final_import.get("importStatus") if isinstance(final_import, dict) else None
            )
            if final_state in _IMPORT_FAILED_STATES:
                final_error = final_import.get("error") if isinstance(final_import, dict) else None
                _emit_import_wait_terminal(
                    err(
                        "IMPORT_FAILED",
                        f"Import finished with status {final_state!r}: {final_error}",
                        "Review the import details and source file, then retry.",
                    )
                )
            table_id = _resolve_table_id(c, table_name)
            _emit_import_wait_terminal(
                ok(
                    {
                        "import": final_import,
                        "import_id": import_id,
                        "table_id": table_id,
                        "preview": preview_data,
                    }
                )
            )
            return
        table_id = _resolve_table_id(c, table_name)
    emit(
        ok(
            {
                "import": created_data,
                "import_id": import_id,
                "table_id": table_id,
                "preview": preview_data,
            }
        )
    )
