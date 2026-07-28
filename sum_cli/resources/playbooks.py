"""`sumcli playbooks ...`"""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.output import emit, ok, truncate_list
from sum_cli.commands import (
    ProfileOption,
    api_client,
    extract_list,
    require_project,
    unwrap_data,
)

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_playbooks(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option("--project")] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/playbooks")
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "playbooks")
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "playbooks": listed["items"],
                "project_id": pid,
                **{k: v for k, v in listed.items() if k != "items"},
            }
        )
    )


@app.command("show")
def show_playbook(
    ctx: typer.Context,
    file_id: Annotated[str, typer.Argument(help="Playbook file id.")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/playbooks/{file_id}")
    emit(ok({"playbook": unwrap_data(body or {}, "data") or body, "project_id": pid}))
