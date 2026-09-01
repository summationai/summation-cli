"""`sumcli files ...`"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

import httpx
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


# A presigned download is served straight from S3 and can run for minutes on a large file, so the
# read timeout is generous while connect/pool stay short. Bytes stream to disk one chunk at a time,
# so CLI memory is constant regardless of file size.
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _download_url_response(ctx, pid: str, file_id: str, profile) -> dict:
    with api_client(ctx, profile) as c:
        try:
            body = c.request("GET", f"/v1/projects/{pid}/files/{file_id}/download-url")
        except ApiError as exc:
            if exc.status != 404:
                raise
            emit_error(
                err(
                    "not_found",
                    f"{file_id} has no raw content to download.",
                    "If this is a document (.sdoc), render it with --format pdf|markdown|docx.",
                )
            )
    data = unwrap_data(body or {}, "data") or body
    return data if isinstance(data, dict) else {}


def _safe_default_name(data: dict, file_id: str) -> str:
    """A safe default download filename from the API's fileName — basename only, never a path.

    The name comes from the file's stored path and is untrusted, so any directory component
    (including ``..`` or an absolute path) is stripped. Falls back to the file id when nothing
    usable remains, so the destination is always a plain name in the working directory.
    """
    raw = data.get("fileName") or data.get("file_name") or ""
    name = Path(str(raw)).name.strip()
    if not name or name in {".", ".."}:
        return file_id
    return name


def _stream_url_to_file(url: str, dest: Path, file_id: str) -> int:
    """Stream a presigned URL's bytes to ``dest`` a chunk at a time. Returns bytes written.

    Writes to a UNIQUE temp file in the destination directory and renames onto ``dest`` on
    success, so a mid-stream failure never leaves a truncated file at ``dest``, and the temp
    file can never clobber an existing sibling the user already has.
    """
    total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    partial = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            with httpx.stream("GET", url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise ApiError(resp.status_code, resp.text)
                for chunk in resp.iter_bytes(_DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
                    total += len(chunk)
        partial.replace(dest)  # atomic on the same filesystem; only a complete file reaches dest
    except (httpx.HTTPError, ApiError) as exc:
        # Any failure — transport drop, or the presigned URL itself 4xx-ing — leaves nothing at
        # dest and no leftover temp file.
        partial.unlink(missing_ok=True)
        emit_error(
            err(
                "DOWNLOAD_FAILED",
                f"Downloading {file_id} failed: {exc}",
                "The download URL is short-lived (~15 min); re-run to mint a fresh one.",
            )
        )
    return total


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

    # Raw bytes stream from a presigned URL straight to disk, so a file of any size (up to GBs)
    # never buffers in the CLI or the API. Rendered documents are small generated artifacts, so
    # they keep the simple buffered render path.
    if format == "raw":
        data = _download_url_response(ctx, pid, file_id, profile)
        url = data.get("url")
        if not url:
            emit_error(
                err(
                    "NO_DOWNLOAD_URL",
                    f"sum-api did not return a download URL for {file_id}.",
                    "Confirm the file exists in the project and your credentials have agent:read.",
                )
            )
        # With -o the caller owns the path (overwriting it is their intent). Without -o, write into
        # a fresh isolated temp dir under the file's own name — matching the old temp-file default:
        # it can neither clobber a file in the working directory nor escape it via a hostile name.
        if output is not None:
            dest = output
        else:
            dest = Path(tempfile.mkdtemp(prefix="sumcli-file-")) / _safe_default_name(data, file_id)
        total = _stream_url_to_file(url, dest, file_id)
        emit(ok({"path": str(dest), "bytes": total, "file_id": file_id, "format": format}))
        return

    with api_client(ctx, profile) as c:
        try:
            raw = c.request_bytes(
                "GET",
                f"/v1/projects/{pid}/reports/{file_id}/content",
                params={"format": format},
            )
        except ApiError as exc:
            # A 404 on the render path is usually a plain file or a wrong id, not an auth problem.
            if exc.status != 404:
                raise
            emit_error(
                err(
                    "not_found",
                    f"{file_id} could not be rendered as {format}.",
                    "If this is a plain file, download it with --format raw (the default).",
                )
            )
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
