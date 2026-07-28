"""`sumcli projects ...`"""

from __future__ import annotations

from typing import Annotated

import typer

from sum_cli.output import (
    action,
    emit,
    emit_error,
    err,
    ok,
    param,
    truncate_list,
)
from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    require_confirm,
    require_project,
    unwrap_data,
)

app = typer.Typer(no_args_is_help=True)

_LIST = action("List projects", "sumcli projects list [--count <count>]")
_SHOW = action("Show project", "sumcli projects show <project-id>")
_CREATE = action(
    "Create project",
    "sumcli projects create --name <name>",
    params={"name": param("Project name")},
)
_SET_CTX = action(
    "Set active default project",
    "sumcli config set-project --project <project-id>",
    params={"project-id": param("Project ID")},
)


def _normalize_projects(body: object) -> list[dict]:
    data = unwrap_data(body or {}, "data")
    if isinstance(data, dict) and "projects" in data:
        items = data["projects"]
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [p for p in items if isinstance(p, dict)]


@app.command("list")
def list_projects(
    ctx: typer.Context,
    count: Annotated[int | None, typer.Option("--count", help="Max items.")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/projects")
    projects = _normalize_projects(body)
    listed = truncate_list(projects, count=count)
    emit(
        ok(
            {"projects": listed["items"], **{k: v for k, v in listed.items() if k != "items"}},
            next_actions=[_SHOW, _CREATE, _SET_CTX],
        )
    )


@app.command("show")
def show_project(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Argument(help="Project ID.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{project_id}")
    emit(ok({"project": unwrap_data(body or {}, "data") or body}, next_actions=[_LIST, _SET_CTX]))


@app.command("current")
def current_project(ctx: typer.Context, profile: ProfileOption = None) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/projects/default")
    project = unwrap_data(body or {}, "data") or body
    pid = project.get("id") if isinstance(project, dict) else None
    emit(
        ok(
            {"project": project},
            next_actions=[
                _SET_CTX if pid else _LIST,
                action(
                    "Show this project",
                    f"sumcli projects show {pid}",
                    params={"project-id": param("Project ID", value=pid)} if pid else {},
                ),
            ],
        )
    )


@app.command("create")
def create_project(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", "-n")],
    description: Annotated[str | None, typer.Option("--description", "-d")] = None,
    profile: ProfileOption = None,
) -> None:
    payload: dict[str, str] = {"name": name}
    if description:
        payload["description"] = description
    with api_client(ctx, profile) as c:
        body = c.request("POST", "/v1/projects", json=payload)
    project = unwrap_data(body or {}, "data") or body
    pid = project.get("id") if isinstance(project, dict) else None
    emit(
        ok(
            {"project": project},
            next_actions=[
                action(
                    "Set as default project",
                    "sumcli config set-project --project <project-id>",
                    params={"project-id": param("Project ID", value=pid)} if pid else {},
                ),
                _LIST,
            ],
        )
    )


@app.command("update")
def update_project(
    ctx: typer.Context,
    project_id: Annotated[str | None, typer.Option("--project")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="Project sharing: private or public."),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project_id)
    if visibility is not None and visibility not in ("private", "public"):
        emit_error(
            err(
                "INVALID_REQUEST",
                f"Invalid --visibility {visibility!r}.",
                "Use 'private' or 'public'.",
            )
        )
    payload: dict[str, str] = {}
    if name:
        payload["name"] = name
    if description:
        payload["description"] = description
    if visibility:
        payload["visibility"] = visibility
    if not payload:
        emit_error(
            err(
                "INVALID_REQUEST",
                "Provide --name, --description, and/or --visibility.",
                "Pass fields to update.",
            )
        )
    with api_client(ctx, profile) as c:
        body = c.request("PATCH", f"/v1/projects/{pid}", json=payload)
    emit(ok({"project": unwrap_data(body or {}, "data") or body}, next_actions=[_SHOW, _LIST]))


@app.command("delete")
def delete_project(
    ctx: typer.Context,
    project_id: Annotated[str | None, typer.Option("--project")] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="projects delete")
    pid = require_project(ctx, project_id)
    with api_client(ctx, profile) as c:
        c.request("DELETE", f"/v1/projects/{pid}", params=api_confirm_params())
    emit(ok({"deleted": pid}, next_actions=[_LIST]))
