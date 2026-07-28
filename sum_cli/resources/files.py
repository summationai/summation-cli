"""`sumcli files ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sum_cli.client import ApiError
from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_fs_delete_params,
    require_confirm,
    require_project,
    unwrap_data,
)
from sum_cli.file_content import read_file_write_payload
from sum_cli.output import emit, emit_error, err, ok, truncate_list
from sum_cli.tempfiles import write_temp_bytes

app = typer.Typer(no_args_is_help=True)


def _files_from_list_response(data: object) -> list:
    """Normalize agent-service list/search payloads to a flat file list."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    entries = data.get("entries")
    if isinstance(entries, list):
        return entries
    files = data.get("files")
    if isinstance(files, list):
        return files
    groups = data.get("groups")
    if isinstance(groups, list):
        flat: list = []
        for group in groups:
            if isinstance(group, dict):
                results = group.get("results")
                if isinstance(results, list):
                    flat.extend(results)
        return flat
    return []


@app.command("list")
def list_files(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option("--project")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q", help="Search query.")] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    params = {"q": query} if query else None
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/files", params=params)
    data = unwrap_data(body or {}, "data")
    items = _files_from_list_response(data if data is not None else body)
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "files": listed["items"],
                "project_id": pid,
                "query": query,
                **{k: v for k, v in listed.items() if k != "items"},
            }
        )
    )


@app.command("show")
def show_file(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/files/{file_id}")
    emit(ok({"file": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("upload")
def upload_file(
    ctx: typer.Context,
    local_path: Annotated[Path, typer.Argument(help="Local file path.")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    remote_path: Annotated[str | None, typer.Option("--path")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    payload: dict = read_file_write_payload(local_path)
    target_path = remote_path or f"/{local_path.name}"
    with api_client(ctx, profile) as c:
        body = c.request(
            "PUT",
            f"/v1/projects/{pid}/files/content",
            params={"path": target_path},
            json=payload,
        )
    emit(ok({"file": unwrap_data(body or {}, "data") or body, "project_id": pid}))


# Rendered download formats. `raw` streams the stored file bytes via the file
# content endpoint (the only option for plain uploads). `pdf`/`markdown`/`docx`
# render an agent-generated document (`.sdoc`) on demand via the report content
# endpoint — these succeed for documents that have no downloadable raw bytes.
_DOWNLOAD_FORMATS = ("raw", "pdf", "markdown", "docx")
_FORMAT_SUFFIX = {"raw": "", "pdf": ".pdf", "markdown": ".md", "docx": ".docx"}


@app.command("download")
def download_file(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help=(
                "Download format: raw stored bytes (default), or render a document "
                "(.sdoc) as pdf, markdown, or docx."
            ),
        ),
    ] = "raw",
    profile: ProfileOption = None,
) -> None:
    """Download a file's content.

    Plain files (uploads) download as raw bytes. Agent-generated documents
    (``.sdoc``) have no raw bytes to stream — pass ``--format pdf|markdown|docx``
    to render them via the report content endpoint.
    """
    if format not in _DOWNLOAD_FORMATS:
        emit_error(
            err(
                "INVALID_FORMAT",
                f"Unknown format {format!r}.",
                f"Pass --format with one of: {', '.join(_DOWNLOAD_FORMATS)}.",
            )
        )
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        try:
            if format == "raw":
                raw = c.request_bytes("GET", f"/v1/projects/{pid}/files/{file_id}/content")
            else:
                raw = c.request_bytes(
                    "GET",
                    f"/v1/projects/{pid}/reports/{file_id}/content",
                    params={"format": format},
                )
        except ApiError as exc:
            # A 404 on either route means the id isn't downloadable that way, not
            # an auth problem. On the raw path it's usually an agent-generated
            # document (.sdoc) with no raw bytes; on the render path it's usually a
            # plain file or a wrong id. Point the caller at the other route instead
            # of letting the generic not-found envelope suggest checking auth.
            if exc.status != 404:
                raise
            if format == "raw":
                message = f"{file_id} has no raw content to download."
                hint = "If this is a document (.sdoc), render it with --format pdf|markdown|docx."
            else:
                message = f"{file_id} could not be rendered as {format}."
                hint = "If this is a plain file, download it with --format raw (the default)."
            emit_error(err("not_found", message, hint))
    if output:
        output.write_bytes(raw)
        dest = str(output)
    else:
        dest = str(write_temp_bytes(prefix="sumcli-file-", suffix=_FORMAT_SUFFIX[format], data=raw))
    emit(ok({"path": dest, "bytes": len(raw), "file_id": file_id, "format": format}))


@app.command("delete")
def delete_file(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="files delete")
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        c.request(
            "DELETE",
            f"/v1/projects/{pid}/files/{file_id}",
            params=api_fs_delete_params(),
        )
    emit(ok({"deleted": file_id, "project_id": pid}))


@app.command("import")
def import_file(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument()],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/projects/{pid}/files/{file_id}/imports", json=None)
    emit(ok({"import": unwrap_data(body or {}, "data") or body, "project_id": pid}))
