"""Shared helpers for Typer resource commands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from sum_cli.client import Client
from sum_cli.config import Config, load
from sum_cli.intent import enforce_intent
from sum_cli.output import action, emit_error, err, invalid_request, param
from sum_cli.project_context import resolve_project

ProfileOption = Annotated[
    str | None,
    typer.Option(
        "--profile",
        envvar="SUMMATION_PROFILE",
        help="Profile to run this command against (overrides the active profile).",
    ),
]


def load_json_object(path: Path, flag: str, *, shape_hint: str) -> dict:
    """Read a JSON object from ``path``, reporting every failure as INVALID_REQUEST.

    ``shape_hint`` is an example of the expected object, shown when the file parses
    but is not an object — each flag expects a different shape.
    """
    try:
        parsed = json.loads(path.read_text())
    except UnicodeDecodeError as exc:
        # Not JSONDecodeError: read_text() fails before parsing on non-UTF-8 bytes.
        invalid_request(
            f"{flag} is not valid UTF-8 text: {exc}", f"Save {flag} as UTF-8 encoded JSON."
        )
    except ValueError as exc:
        # Subsumes json.JSONDecodeError.
        invalid_request(f"Invalid JSON in {flag}: {exc}", f"Provide a valid JSON object in {flag}.")
    except OSError as exc:
        invalid_request(
            f"Cannot read {flag}: {exc}", f"Check that the {flag} path exists and is readable."
        )
    if not isinstance(parsed, dict):
        invalid_request(f"{flag} must contain a JSON object.", f"Use an object, e.g. {shape_hint}.")
    return parsed


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


def get_intent(ctx: typer.Context) -> str | None:
    """Resolved intent for this invocation. A plain getter — see ``require_intent``."""
    return getattr(ctx.obj, "intent", None) if ctx.obj is not None else None


def require_intent(ctx: typer.Context) -> str | None:
    """Enforce the intent contract, then return the value.

    Called where a command actually reaches sum-api rather than from the root
    callback. Click never runs a command body for ``--help``, so gating here makes
    the help and discovery exemptions follow from control flow — no code has to
    decide which argv token is a flag and which is an option value.
    """
    intent = get_intent(ctx)
    enforce_intent(intent, subcommand=ctx.find_root().invoked_subcommand)
    return intent


@contextmanager
def api_client(ctx: typer.Context, profile: str | None = None) -> Iterator[Client]:
    client = Client(cfg=get_config(ctx, profile), intent=require_intent(ctx))
    try:
        yield client
    finally:
        client.close()


def require_project(
    ctx: typer.Context,
    project: str | None = None,
) -> str:
    # Intent first: it is a precondition of the whole invocation, so it must not
    # depend on whether a command happens to resolve its project before it builds
    # a client. Idempotent, so the later api_client call re-checks harmlessly.
    require_intent(ctx)
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
