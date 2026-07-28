"""`sumcli reports ...` — generate and verify (list/export/delete use ``files``)."""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.commands import ProfileOption, api_client, require_project, unwrap_data
from sum_cli.output import action, emit, ok, param
from sum_cli.stream_options import (
    OptionalFollowOption,
    WaitOption,
    post_with_wait_follow,
    resolve_follow,
)

app = typer.Typer(no_args_is_help=True)

_LIST_FILES = action("List files", "sumcli files list [--query <query>]")
_DOWNLOAD = action(
    "Download file",
    "sumcli files download <file-id>",
    params={"file-id": param("File ID")},
)
_DELETE = action(
    "Delete file",
    "sumcli files delete <file-id> --confirm",
    params={"file-id": param("File ID")},
)


@app.command("show")
def show_report(
    ctx: typer.Context,
    report_id: Annotated[str, typer.Argument(help="Report or document file ID.")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/reports/{report_id}")
    emit(
        ok(
            {"report": unwrap_data(body or {}, "data") or body, "file_id": report_id},
            next_actions=[
                _DOWNLOAD,
                action(
                    "Download this file",
                    f"sumcli files download {report_id}",
                    params={"file-id": param("File ID", value=report_id)},
                ),
                action(
                    "Verify",
                    f"sumcli reports verify {report_id}",
                    params={"file-id": param("File ID", value=report_id)},
                ),
            ],
        )
    )


@app.command("generate")
def generate_report(
    ctx: typer.Context,
    message: Annotated[str, typer.Option("--message", "-m")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    wait: WaitOption = True,
    follow: OptionalFollowOption = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    follow = resolve_follow(wait=wait, follow=follow, default=True)
    path = f"/v1/projects/{pid}/reports/generations"
    payload = {"message": message}
    with api_client(ctx, profile) as c:
        outcome = post_with_wait_follow(
            c,
            "POST",
            path,
            wait=wait,
            follow=follow,
            json=payload,
            result_builder=lambda p, t: {"text": t, "payload": p},
        )
        if outcome.streamed:
            return
        body = outcome.body
    result = unwrap_data(body or {}, "data") or body
    file_id = None
    if isinstance(result, dict):
        file_id = result.get("id") or result.get("file_id")
    next_actions = [_LIST_FILES]
    if file_id:
        next_actions.extend(
            [
                action(
                    "Show file",
                    f"sumcli files show {file_id}",
                    params={"file-id": param("File ID", value=file_id)},
                ),
                action(
                    "Verify",
                    f"sumcli reports verify {file_id}",
                    params={"file-id": param("File ID", value=file_id)},
                ),
            ]
        )
    emit(ok({"report": result}, next_actions=next_actions))


@app.command("verify")
def verify_report(
    ctx: typer.Context,
    report_id: Annotated[str, typer.Argument(help="Report or document file ID.")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    instructions: Annotated[str | None, typer.Option("--instructions")] = None,
    wait: WaitOption = True,
    follow: OptionalFollowOption = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    follow = resolve_follow(wait=wait, follow=follow, default=True)
    path = f"/v1/projects/{pid}/reports/{report_id}/verifications"
    payload: dict = {}
    if instructions:
        payload["instructions"] = instructions
    with api_client(ctx, profile) as c:
        outcome = post_with_wait_follow(
            c,
            "POST",
            path,
            wait=wait,
            follow=follow,
            json=payload,
            result_builder=lambda p, t: {"text": t, "payload": p},
        )
        if outcome.streamed:
            return
        body = outcome.body
    emit(
        ok(
            {"verification": unwrap_data(body or {}, "data") or body, "file_id": report_id},
            next_actions=[
                _LIST_FILES,
                action(
                    "Download file",
                    f"sumcli files download {report_id}",
                    params={"file-id": param("File ID", value=report_id)},
                ),
            ],
        )
    )
