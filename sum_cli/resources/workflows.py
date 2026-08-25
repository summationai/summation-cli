"""`sumcli workflows ...`"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from sum_cli.commands import (
    ProfileOption,
    api_client,
    extract_list,
    load_json_object,
    require_project,
    unwrap_data,
)
from sum_cli.output import emit, emit_error, err, invalid_request, ok

app = typer.Typer(no_args_is_help=True)

_CREATE_STATUSES = ("draft", "paused", "archived")
_UPDATE_STATUSES = ("draft", "active", "paused", "archived")
_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_DEFAULT_RUN_PAGE_SIZE = 5


def _load_json_array(path: Path, flag: str, *, shape_hint: str) -> list:
    """Read a JSON array from ``path``, reporting failures as INVALID_REQUEST."""
    try:
        parsed = json.loads(path.read_text())
    except UnicodeDecodeError as exc:
        invalid_request(
            f"{flag} is not valid UTF-8 text: {exc}", f"Save {flag} as UTF-8 encoded JSON."
        )
    except ValueError as exc:
        invalid_request(f"Invalid JSON in {flag}: {exc}", f"Provide a valid JSON array in {flag}.")
    except OSError as exc:
        invalid_request(
            f"Cannot read {flag}: {exc}", f"Check that the {flag} path exists and is readable."
        )
    if not isinstance(parsed, list):
        invalid_request(f"{flag} must contain a JSON array.", f"Use an array, e.g. {shape_hint}.")
    return parsed


def _unwrap_workflow_document(raw: dict) -> dict:
    """Accept a bare workflow, ``{data: ...}``, or a CLI ``show`` result envelope."""
    if isinstance(raw.get("result"), dict):
        raw = raw["result"]
    if isinstance(raw.get("data"), dict):
        raw = raw["data"]
    if isinstance(raw.get("workflow"), dict):
        raw = raw["workflow"]
    return raw


def _field(doc: dict, *names: str) -> object | None:
    for name in names:
        if name in doc and doc[name] is not None:
            return doc[name]
    return None


def _refuse_unconfirmed_activate(workflow_id: str) -> NoReturn:
    emit_error(
        err(
            "CONFIRM_REQUIRED",
            f"Activating workflow {workflow_id} freezes its graph and can start sending "
            "real email/Slack on its schedule.",
            "Re-run with --confirm to activate.",
        )
    )


def _refuse_unconfirmed_run(workflow_id: str) -> NoReturn:
    emit_error(
        err(
            "CONFIRM_REQUIRED",
            f"Running workflow {workflow_id} now executes its delivery steps for real "
            "(email/Slack to the recipients the graph names).",
            "Re-run with --confirm to run now.",
        )
    )


def _build_write_payload(
    *,
    project_id: str,
    title: str,
    description: str | None,
    status: str | None,
    output_folder: str | None,
    graph: dict | None,
    triggers: list | None,
    expected_revision: int | None = None,
) -> dict:
    payload: dict = {
        "project_id": project_id,
        "title": title,
    }
    if description is not None:
        payload["description"] = description
    if status is not None:
        payload["status"] = status
    if output_folder is not None:
        payload["output_folder"] = output_folder
    if graph is not None:
        payload["graph"] = graph
    if triggers is not None:
        payload["triggers"] = triggers
    if expected_revision is not None:
        payload["expected_revision"] = expected_revision
    return payload


TitleOption = Annotated[str, typer.Option("--title", help="Workflow name.")]
OptionalTitleOption = Annotated[
    str | None, typer.Option("--title", help="Workflow name. Defaults from --body-file or show.")
]
DescriptionOption = Annotated[
    str | None, typer.Option("--description", help="Optional longer description.")
]
OutputFolderOption = Annotated[
    str | None,
    typer.Option("--output-folder", help="Project folder for outputs, e.g. /Reports."),
]
GraphFileOption = Annotated[
    Path | None,
    typer.Option(
        "--graph-file",
        help='JSON object {"nodes": [...], "edges": [...]} for the typed graph.',
    ),
]
TriggersFileOption = Annotated[
    Path | None,
    typer.Option(
        "--triggers-file",
        help="JSON array of schedule triggers. On update, omit the flag to keep "
        "triggers from show/GET; an empty array deletes them all.",
    ),
]
ExpectedRevisionOption = Annotated[
    int,
    typer.Option(
        "--expected-revision",
        min=0,
        help="Revision from the last show/list; refused if the workflow moved on.",
    ),
]
PageTokenOption = Annotated[
    str | None, typer.Option("--page-token", help="Token from a previous page's next_page_token.")
]


@app.command("list")
def list_workflows(
    ctx: typer.Context,
    project: Annotated[
        str | None, typer.Option("--project", help="Filter to project-scoped workflows.")
    ] = None,
    page_token: PageTokenOption = None,
    page_size: Annotated[
        int | None,
        typer.Option("--page-size", min=1, max=_MAX_PAGE_SIZE, help="Workflows per page (1-100)."),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    params: dict = {}
    if project:
        params["project_id"] = project
    if page_token:
        params["page_token"] = page_token
    params["page_size"] = page_size if page_size is not None else _DEFAULT_PAGE_SIZE
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/workflows", params=params)
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "workflows")
    result: dict = {"workflows": items}
    if isinstance(data, dict) and data.get("nextPageToken") is not None:
        result["next_page_token"] = data["nextPageToken"]
    elif isinstance(data, dict) and data.get("next_page_token") is not None:
        result["next_page_token"] = data["next_page_token"]
    emit(ok(result))


@app.command("show")
def show_workflow(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/workflows/{workflow_id}")
    emit(ok({"workflow": unwrap_data(body or {}, "data") or body}))


@app.command("create")
def create_workflow(
    ctx: typer.Context,
    title: TitleOption,
    project: Annotated[str | None, typer.Option("--project")] = None,
    description: DescriptionOption = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            help=f"Initial status (not active). One of: {', '.join(_CREATE_STATUSES)}.",
        ),
    ] = None,
    output_folder: OutputFolderOption = None,
    graph_file: GraphFileOption = None,
    triggers_file: TriggersFileOption = None,
    profile: ProfileOption = None,
) -> None:
    if status is not None and status not in _CREATE_STATUSES:
        invalid_request(
            f"Invalid --status {status!r}.",
            f"Use one of: {', '.join(_CREATE_STATUSES)}. Activate with `workflows activate`.",
        )
    pid = require_project(ctx, project)
    graph = (
        load_json_object(graph_file, "--graph-file", shape_hint='{"nodes": [], "edges": []}')
        if graph_file is not None
        else None
    )
    triggers = (
        _load_json_array(triggers_file, "--triggers-file", shape_hint="[{...}]")
        if triggers_file is not None
        else None
    )
    payload = _build_write_payload(
        project_id=pid,
        title=title,
        description=description,
        status=status,
        output_folder=output_folder,
        graph=graph,
        triggers=triggers,
    )
    with api_client(ctx, profile) as c:
        body = c.request("POST", "/v1/workflows", json=payload)
    emit(ok({"workflow": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("update")
def update_workflow(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    expected_revision: ExpectedRevisionOption,
    title: OptionalTitleOption = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    description: DescriptionOption = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            help=f"Workflow state. One of: {', '.join(_UPDATE_STATUSES)}. "
            "active is an echo only — use activate to go live.",
        ),
    ] = None,
    output_folder: OutputFolderOption = None,
    graph_file: GraphFileOption = None,
    triggers_file: TriggersFileOption = None,
    body_file: Annotated[
        Path | None,
        typer.Option(
            "--body-file",
            help="JSON workflow from show/GET for a read-modify-write PUT. "
            "Flag overrides win. Triggers omitted from the file are deleted.",
        ),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    if status is not None and status not in _UPDATE_STATUSES:
        invalid_request(
            f"Invalid --status {status!r}.",
            f"Use one of: {', '.join(_UPDATE_STATUSES)}.",
        )

    # Validate local files before any HTTP call (same ordering as create / schedules).
    body_doc: dict | None = None
    if body_file is not None:
        body_doc = _unwrap_workflow_document(
            load_json_object(body_file, "--body-file", shape_hint='{"title": "..."}')
        )
    graph_override: dict | None = None
    if graph_file is not None:
        graph_override = load_json_object(
            graph_file, "--graph-file", shape_hint='{"nodes": [], "edges": []}'
        )
    triggers_override: list | None = None
    if triggers_file is not None:
        triggers_override = _load_json_array(
            triggers_file, "--triggers-file", shape_hint="[{...}]"
        )

    with api_client(ctx, profile) as c:
        if body_doc is not None:
            base = body_doc
        else:
            existing = c.request("GET", f"/v1/workflows/{workflow_id}")
            current = unwrap_data(existing or {}, "data") or existing
            if not isinstance(current, dict) or not current:
                invalid_request(
                    f"Workflow {workflow_id} could not be loaded for update.",
                    "Pass --body-file from `sumcli workflows show`, "
                    "or retry when the API returns the workflow.",
                )
            base = current

        resolved_project = project or _field(base, "project_id", "projectId")
        if not isinstance(resolved_project, str) or not resolved_project:
            # Do not fall back to the profile default project: that value is unrelated
            # to this workflow and a full-replace PUT would reassign it.
            invalid_request(
                f"Workflow {workflow_id} has no project id to reuse.",
                "Pass --project, or supply a --body-file carrying projectId "
                "(the unedited output of `sumcli workflows show` works as-is).",
            )

        resolved_title = title or _field(base, "title")
        if not isinstance(resolved_title, str) or not resolved_title:
            invalid_request(
                "Workflow update needs a title.",
                "Pass --title, or supply a --body-file carrying title "
                "(the unedited output of `sumcli workflows show` works as-is).",
            )

        if triggers_override is not None:
            triggers: list | None = triggers_override
        else:
            existing_triggers = _field(base, "triggers")
            # A missing triggers key must not become [] — that deletes every schedule.
            # Only echo a list the GET/body actually carried.
            triggers = existing_triggers if isinstance(existing_triggers, list) else None

        if graph_override is not None:
            graph: dict | None = graph_override
        elif body_file is not None:
            existing_graph = _field(base, "graph")
            graph = existing_graph if isinstance(existing_graph, dict) else None
        else:
            # Flag-only update: leave the stored graph untouched unless --graph-file.
            graph = None

        # description and output_folder default to "" on the wire — omitting them resets
        # them. Carry both forward from the GET/body whenever the caller did not pass a flag.
        resolved_description = description
        if resolved_description is None:
            desc = _field(base, "description")
            resolved_description = desc if isinstance(desc, str) else None

        resolved_status = status
        if resolved_status is None and body_file is not None:
            st = _field(base, "status")
            resolved_status = st if isinstance(st, str) else None

        resolved_folder = output_folder
        if resolved_folder is None:
            folder = _field(base, "output_folder", "outputFolder")
            resolved_folder = folder if isinstance(folder, str) else None

        payload = _build_write_payload(
            project_id=resolved_project,
            title=resolved_title,
            description=resolved_description,
            status=resolved_status,
            output_folder=resolved_folder,
            graph=graph,
            triggers=triggers,
            expected_revision=expected_revision,
        )
        body = c.request("PUT", f"/v1/workflows/{workflow_id}", json=payload)
    emit(
        ok(
            {
                "workflow": unwrap_data(body or {}, "data") or body,
                "project_id": resolved_project,
            }
        )
    )


@app.command("activate")
def activate_workflow(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    expected_revision: ExpectedRevisionOption,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    if not confirm:
        _refuse_unconfirmed_activate(workflow_id)
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/workflows/{workflow_id}/activate",
            json={"expected_revision": expected_revision},
        )
    data = unwrap_data(body or {}, "data") or body
    emit(ok({"activation": data, "workflow_id": workflow_id}))


@app.command("versions")
def list_workflow_versions(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    page_token: PageTokenOption = None,
    profile: ProfileOption = None,
) -> None:
    params: dict = {}
    if page_token:
        params["page_token"] = page_token
    with api_client(ctx, profile) as c:
        body = c.request(
            "GET",
            f"/v1/workflows/{workflow_id}/versions",
            params=params or None,
        )
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "versions")
    result: dict = {"versions": items, "workflow_id": workflow_id}
    if isinstance(data, dict):
        token = data.get("nextPageToken", data.get("next_page_token"))
        if token is not None:
            result["next_page_token"] = token
    emit(ok(result))


@app.command("runs")
def list_workflow_runs(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    page_token: PageTokenOption = None,
    page_size: Annotated[
        int | None,
        typer.Option("--page-size", min=1, max=_MAX_PAGE_SIZE, help="Runs per page (1-100)."),
    ] = None,
    profile: ProfileOption = None,
) -> None:
    params: dict = {
        "page_size": page_size if page_size is not None else _DEFAULT_RUN_PAGE_SIZE,
    }
    if page_token:
        params["page_token"] = page_token
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/workflows/{workflow_id}/runs", params=params)
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "runs")
    result: dict = {"runs": items, "workflow_id": workflow_id}
    if isinstance(data, dict):
        token = data.get("nextPageToken", data.get("next_page_token"))
        if token is not None:
            result["next_page_token"] = token
    emit(ok(result))


@app.command("run-show")
def show_workflow_run(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/workflows/{workflow_id}/runs/{run_id}")
    emit(
        ok(
            {
                "run": unwrap_data(body or {}, "data") or body,
                "workflow_id": workflow_id,
                "run_id": run_id,
            }
        )
    )


@app.command("run")
def run_workflow(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument(help="Workflow id.")],
    version: Annotated[
        str | None,
        typer.Option(
            "--version",
            help="Activated version id (activeVersionId from show). "
            "Required for graph workflows; fetched from show when omitted.",
        ),
    ] = None,
    request_id: Annotated[
        str | None,
        typer.Option(
            "--request-id",
            help="Idempotency UUID. Defaults to a fresh UUID; retry with the same value.",
        ),
    ] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    if not confirm:
        _refuse_unconfirmed_run(workflow_id)
    rid = request_id or str(uuid.uuid4())
    try:
        uuid.UUID(rid)
    except ValueError:
        invalid_request(
            f"Invalid --request-id {rid!r}: expected a UUID.",
            "Pass a UUID, e.g. 550e8400-e29b-41d4-a716-446655440000.",
        )
    with api_client(ctx, profile) as c:
        resolved_version = version
        if not resolved_version:
            existing = c.request("GET", f"/v1/workflows/{workflow_id}")
            current = unwrap_data(existing or {}, "data") or existing
            if isinstance(current, dict):
                active = _field(current, "activeVersionId", "active_version_id")
                if isinstance(active, str) and active:
                    resolved_version = active
        if not resolved_version:
            invalid_request(
                f"Workflow {workflow_id} has no active version to run.",
                "Activate it first, or pass --version with an activated version id.",
            )
        payload = {"request_id": rid, "workflow_version_id": resolved_version}
        body = c.request("POST", f"/v1/workflows/{workflow_id}/runs", json=payload)
    emit(
        ok(
            {
                "run": unwrap_data(body or {}, "data") or body,
                "workflow_id": workflow_id,
                "request_id": rid,
                "workflow_version_id": resolved_version,
            }
        )
    )


@app.command("node-types")
def list_workflow_node_types(
    ctx: typer.Context,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/workflows/node-types")
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "node_types", "nodeTypes")
    emit(ok({"node_types": items}))
