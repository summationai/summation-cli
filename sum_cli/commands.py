"""Shared helpers for Typer resource commands."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from sum_cli.client import Client
from sum_cli.config import Config, load
from sum_cli.output import action, emit_error, err, param
from sum_cli.project_context import resolve_project

ProfileOption = Annotated[
    str | None,
    typer.Option(
        "--profile",
        envvar="SUMMATION_PROFILE",
        help="Profile to run this command against (overrides the active profile).",
    ),
]

_SET_CONTEXT = action(
    "Set default project for active profile",
    "sumcli config set-project --project <project-id>",
    params={"project-id": param("Project ID")},
)
_LIST_PROJECTS = action("List projects", "sumcli projects list")


def get_config(ctx: typer.Context, profile: str | None = None) -> Config:
    if ctx.obj is not None and hasattr(ctx.obj, "config"):
        return ctx.obj.config(profile=profile)
    return load(profile=profile)


@contextmanager
def api_client(ctx: typer.Context, profile: str | None = None) -> Iterator[Client]:
    client = Client(cfg=get_config(ctx, profile))
    try:
        yield client
    finally:
        client.close()


def require_project(
    ctx: typer.Context,
    project: str | None = None,
) -> str:
    resolved = resolve_project(get_config(ctx), explicit=project)
    if not resolved:
        emit_error(
            err(
                "NO_PROJECT",
                "No project specified.",
                "Run `sumcli config set-project --project <id>` or pass --project.",
                next_actions=[_SET_CONTEXT, _LIST_PROJECTS],
            )
        )
    return resolved


def require_confirm(confirm: bool, *, action_name: str) -> None:
    if not confirm:
        emit_error(
            err(
                "CONFIRM_REQUIRED",
                f"Destructive action '{action_name}' requires --confirm.",
                f"Re-run with --confirm to proceed with {action_name}.",
            )
        )


def api_confirm_params() -> dict[str, bool]:
    """Query params for destructive public DELETE endpoints (``confirm=true``)."""
    return {"confirm": True}


def api_fs_delete_params() -> dict[str, bool]:
    """Query params for DELETE ``/v1/projects/.../files/{id}`` (proxies to agent ``/fs``).

    Documents (``.sdoc``), runbooks, and other folder-like kinds contain child
    files; agent-service requires ``recursive=true`` to delete them.
    """
    return {"recursive": True, "confirm": True}


def unwrap_data(body: object, *keys: str) -> object | None:
    """Drill into common {data: {...}} response shapes."""
    cur: object = body
    for key in keys:
        if not isinstance(cur, dict):
            return None
        if key not in cur:
            return None
        cur = cur[key]
    return cur


def extract_list(data: object, *item_keys: str) -> list:
    """Normalize API list payloads to a list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in item_keys:
            items = data.get(key)
            if isinstance(items, list):
                return items
    return []
