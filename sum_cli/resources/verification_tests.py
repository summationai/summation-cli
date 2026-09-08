"""`sumcli verification-tests ...` — custom verification policy management."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Any, NoReturn
from urllib.parse import unquote

import typer
import yaml
from pydantic import ValidationError

from sum_cli.commands import (
    ProfileOption,
    api_client,
    require_confirm,
    require_project,
    unwrap_data,
)
from sum_cli.output import action, emit, emit_error, err, invalid_request, ok, param
from sum_cli.verification_contract import CUSTOM_TEST_SUBJECT_TYPES, CustomTestBundle

app = typer.Typer(no_args_is_help=True)

_TARGET_ORG_HEADER = "x-agent-proxy-target-org"
_SCOPES = ("tenant", "project", "artifact")


class Scope(str, Enum):
    tenant = "tenant"
    project = "project"
    artifact = "artifact"


class SubjectType(str, Enum):
    html = "html"
    document = "document"
    deck = "deck"
    workbook = "workbook"


class AttachmentOperation(str, Enum):
    add = "add"
    remove = "remove"


BundleOption = Annotated[
    Path,
    typer.Option(
        "--bundle",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Custom-test bundle YAML or JSON file.",
    ),
]
TargetOrgOption = Annotated[
    str | None,
    typer.Option(
        "--target-org",
        help="Target organization for an authorized audited cross-org operator call.",
    ),
]
ProjectOption = Annotated[str | None, typer.Option("--project", help="Project scope id.")]
ArtifactOption = Annotated[str | None, typer.Option("--artifact", help="Artifact/file scope id.")]


def _bundle_error(message: str, *, data: Any = None) -> NoReturn:
    emit_error(
        err(
            "INVALID_BUNDLE",
            message,
            "Fix the bundle and re-run `sumcli verification-tests validate --bundle FILE`.",
            data=data,
        )
    )


def _load_bundle(path: Path) -> CustomTestBundle:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        _bundle_error(f"{path} is not valid UTF-8 text: {exc}")
    except OSError as exc:
        _bundle_error(f"Cannot read bundle {path}: {exc}")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _bundle_error(f"Invalid YAML/JSON in {path}: {exc}")
    if not isinstance(raw, dict):
        _bundle_error(f"Bundle {path} must contain an object at the top level.")
    try:
        return CustomTestBundle.model_validate(raw)
    except ValidationError as exc:
        details = []
        for item in exc.errors(include_url=False, include_context=False):
            location = ".".join(str(part) for part in item.get("loc", ())) or "bundle"
            details.append(f"{location}: {item.get('msg', 'invalid value')}")
        _bundle_error("; ".join(details), data={"errors": details})


def _target_headers(target_org: str | None) -> dict[str, str] | None:
    return {_TARGET_ORG_HEADER: target_org} if target_org else None


def _nonblank(value: str | None, *, flag: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        invalid_request(
            f"{flag} must not be blank.",
            f"Pass a non-blank value for {flag}, or omit the flag.",
        )
    return normalized


def _path_segment(value: str, *, flag: str) -> str:
    normalized = value.strip()
    decoded = unquote(normalized)
    if (
        not normalized
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or "?" in decoded
        or "#" in decoded
        or any(character.isspace() or ord(character) < 32 for character in decoded)
    ):
        invalid_request(
            f"{flag} must be one non-blank path segment.",
            f"Pass the id only for {flag}, without URL delimiters or whitespace.",
        )
    return normalized


def _target_org(value: str | None) -> str | None:
    return _path_segment(value, flag="--target-org") if value is not None else None


def _request_preview(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
    target_org: str | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "method": method,
        "path": path,
    }
    if params is not None:
        request["query"] = params
    request["headers"] = _target_headers(target_org) or {}
    if body is not None:
        request["body"] = body
    return request


def _required_data(body: object, *, expected: str) -> object:
    data = unwrap_data(body, "data")
    if data is None:
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                f"Response has no data field containing {expected}.",
                "The API response shape changed. Upgrade sumcli, or report this with "
                "sumcli --version output.",
            )
        )
    return data


def _required_dict(body: object, *, expected: str) -> dict[str, Any]:
    data = _required_data(body, expected=expected)
    if not isinstance(data, dict):
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                f"Response data for {expected} is {type(data).__name__}, not an object.",
                "The API response shape changed. Upgrade sumcli, or report this with "
                "sumcli --version output.",
            )
        )
    return data


def _required_list(body: object, *, expected: str) -> list[Any]:
    data = _required_data(body, expected=expected)
    if not isinstance(data, list):
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                f"Response data for {expected} is {type(data).__name__}, not a list.",
                "The API response shape changed. Upgrade sumcli, or report this with "
                "sumcli --version output.",
            )
        )
    return data


def _required_list_field(
    body: dict[str, Any],
    field: str,
    *,
    expected: str,
) -> list[Any]:
    value = body.get(field)
    if not isinstance(value, list):
        actual = "missing" if field not in body else type(value).__name__
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                f"Response field {field!r} for {expected} is {actual}, not a list.",
                "The API response shape changed. Upgrade sumcli, or report this with "
                "sumcli --version output.",
            )
        )
    return value


def _definition_list_metadata(body: object) -> tuple[int, int, bool]:
    if not isinstance(body, dict):
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                "Verification-test list response is not an object with pagination metadata.",
                "The API response shape changed. Upgrade sumcli, or report this with "
                "sumcli --version output.",
            )
        )
    showing = body.get("showing")
    total = body.get("total")
    truncated = body.get("truncated")
    if type(showing) is not int or type(total) is not int or type(truncated) is not bool:
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                "Verification-test list response is missing valid showing, total, or "
                "truncated metadata.",
                "The API response shape changed. Upgrade sumcli, or report this with "
                "sumcli --version output.",
            )
        )
    return showing, total, truncated


def _scope_params(
    ctx: typer.Context,
    *,
    scope: Scope,
    project: str | None,
    artifact: str | None,
    target_org: str | None,
    profile: str | None,
) -> tuple[str, dict[str, str]]:
    scope_value = scope.value
    if scope_value == "tenant":
        if project is not None or artifact is not None:
            invalid_request(
                "Tenant scope does not accept --project or --artifact.",
                "Remove the id flag; tenant scope derives the authenticated organization.",
            )
        return scope_value, {"scope": scope_value}

    if scope_value == "project":
        if artifact is not None:
            invalid_request(
                "Project scope does not accept --artifact.",
                "Pass --project, or omit it to use the profile default for a same-org call.",
            )
        if target_org and not project:
            invalid_request(
                "Cross-org project scope requires an explicit --project.",
                "Pass --project ID together with --target-org; profile defaults are not "
                "portable across orgs.",
            )
        if project is not None:
            project = _path_segment(project, flag="--project")
        project_id = _path_segment(
            require_project(ctx, project, profile=profile),
            flag="--project",
        )
        return scope_value, {"scope": scope_value, "scope_id": project_id}

    if project is not None:
        invalid_request(
            "Artifact scope does not accept --project.",
            "Pass the artifact file id with --artifact.",
        )
    if artifact is None:
        invalid_request(
            "Artifact scope requires --artifact.",
            "Pass --artifact FILE_ID.",
        )
    return scope_value, {
        "scope": scope_value,
        "scope_id": _path_segment(artifact, flag="--artifact"),
    }


def _attachment_body(
    *,
    scope_params: dict[str, str],
    subject_type: SubjectType,
    op: AttachmentOperation,
    custom_test_id: str | None,
    target_ref: str | None,
) -> dict[str, Any]:
    custom_test_id = _nonblank(custom_test_id, flag="--custom-test-id")
    target_ref = _nonblank(target_ref, flag="--target-ref")
    if op is AttachmentOperation.add:
        if not custom_test_id:
            invalid_request(
                "An add overlay requires --custom-test-id.",
                "Pass --op add --custom-test-id CVT_ID and omit --target-ref.",
            )
        if target_ref:
            invalid_request(
                "An add overlay cannot include --target-ref.",
                "Use --custom-test-id for add, or switch to --op remove.",
            )
    else:
        if not target_ref:
            invalid_request(
                "A removal overlay requires --target-ref.",
                "Pass --op remove --target-ref CHECK_OR_TEST_REF and omit --custom-test-id.",
            )
        if custom_test_id:
            invalid_request(
                "A removal overlay cannot include --custom-test-id.",
                "Use --target-ref for remove, or switch to --op add.",
            )
        if len(target_ref) > 255:
            invalid_request(
                "--target-ref must be at most 255 characters.",
                "Pass the resolved check or test ref only.",
            )
    return {
        **scope_params,
        "subject_type": subject_type.value,
        "op": op.value,
        **({"custom_test_id": custom_test_id} if custom_test_id else {}),
        **({"target_ref": target_ref} if target_ref else {}),
    }


@app.command("validate")
def validate_bundle(bundle: BundleOption) -> None:
    """Validate a custom-test bundle locally without loading a profile or using the network."""
    parsed = _load_bundle(bundle)
    emit(
        ok(
            {
                "valid": True,
                "bundle": str(bundle),
                "version": parsed.version,
                "subject_type": parsed.subject_type,
                "test_count": len(parsed.tests),
                "category_count": len({test.category for test in parsed.tests}),
            },
            next_actions=[
                action(
                    "Upload this bundle",
                    "sumcli verification-tests upload --bundle <file>",
                    params={"file": param("Bundle path", value=str(bundle))},
                )
            ],
        )
    )


@app.command("upload")
def upload_bundle(
    ctx: typer.Context,
    bundle: BundleOption,
    target_org: TargetOrgOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate and emit the exact request; send nothing.")
    ] = False,
    profile: ProfileOption = None,
) -> None:
    parsed = _load_bundle(bundle)
    target_org = _target_org(target_org)
    body = parsed.model_dump(mode="json")
    if dry_run:
        emit(
            ok(
                {
                    "dry_run": True,
                    "request": _request_preview(
                        "POST", "/v1/verification-tests", body=body, target_org=target_org
                    ),
                }
            )
        )
        return
    with api_client(ctx, profile) as client:
        response = client.request(
            "POST",
            "/v1/verification-tests",
            json=body,
            headers=_target_headers(target_org),
        )
    data = _required_dict(response, expected="created verification tests")
    created = _required_list_field(data, "created", expected="created verification tests")
    emit(
        ok(
            {"created": created, "target_org": target_org},
            next_actions=[
                action(
                    "Attach a created test",
                    "sumcli verification-tests attach --scope project --subject-type "
                    "<type> --op add --custom-test-id <id>",
                ),
                action("List definitions", "sumcli verification-tests list"),
                action(
                    "Preview the effective set",
                    "sumcli verification-tests preview --scope project --subject-type <type>",
                ),
            ],
        )
    )


@app.command("list")
def list_definitions(
    ctx: typer.Context,
    subject_type: Annotated[SubjectType | None, typer.Option("--subject-type")] = None,
    count: Annotated[int | None, typer.Option("--count", min=1, max=100)] = None,
    target_org: TargetOrgOption = None,
    profile: ProfileOption = None,
) -> None:
    target_org = _target_org(target_org)
    params: dict[str, Any] = {}
    if subject_type is not None:
        params["subject_type"] = subject_type.value
    if count is not None:
        params["limit"] = count
    with api_client(ctx, profile) as client:
        response = client.request(
            "GET",
            "/v1/verification-tests",
            params=params,
            headers=_target_headers(target_org),
        )
    tests = _required_list(response, expected="verification-test list")
    showing, total, truncated = _definition_list_metadata(response)
    emit(
        ok(
            {
                "tests": tests,
                "showing": showing,
                "total": total,
                "truncated": truncated,
                "target_org": target_org,
            }
        )
    )


@app.command("attach")
def attach(
    ctx: typer.Context,
    scope: Annotated[Scope, typer.Option("--scope")],
    subject_type: Annotated[SubjectType, typer.Option("--subject-type")],
    op: Annotated[AttachmentOperation, typer.Option("--op")],
    project: ProjectOption = None,
    artifact: ArtifactOption = None,
    custom_test_id: Annotated[str | None, typer.Option("--custom-test-id")] = None,
    target_ref: Annotated[str | None, typer.Option("--target-ref")] = None,
    target_org: TargetOrgOption = None,
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="Confirm an --op remove overlay, which suppresses a resolved test for the scope.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate and emit the exact request; send nothing.")
    ] = False,
    profile: ProfileOption = None,
) -> None:
    target_org = _target_org(target_org)
    _, scope_query = _scope_params(
        ctx,
        scope=scope,
        project=project,
        artifact=artifact,
        target_org=target_org,
        profile=profile,
    )
    body = _attachment_body(
        scope_params=scope_query,
        subject_type=subject_type,
        op=op,
        custom_test_id=custom_test_id,
        target_ref=target_ref,
    )
    # The API requires confirm=true for a removal overlay because it suppresses a
    # test that currently runs for the scope. Add overlays are not gated.
    remove = op is AttachmentOperation.remove
    request_kwargs: dict[str, Any] = {"params": {"confirm": True}} if remove else {}
    if dry_run:
        emit(
            ok(
                {
                    "dry_run": True,
                    "request": _request_preview(
                        "POST",
                        "/v1/verification-tests/attachments",
                        body=body,
                        target_org=target_org,
                        **request_kwargs,
                    ),
                }
            )
        )
        return
    if remove:
        require_confirm(confirm, action_name="verification-tests attach")
    with api_client(ctx, profile) as client:
        response = client.request(
            "POST",
            "/v1/verification-tests/attachments",
            json=body,
            headers=_target_headers(target_org),
            **request_kwargs,
        )
    attachment = _required_dict(response, expected="created verification-test attachment")
    emit(
        ok(
            {"attachment": attachment, "target_org": target_org},
            next_actions=[
                action(
                    "Preview the effective set",
                    "sumcli verification-tests preview --scope <scope> --subject-type <type>",
                ),
                action(
                    "List attachment ids",
                    "sumcli verification-tests list-attachments --scope <scope> "
                    "--subject-type <type>",
                ),
                action(
                    "Detach this attachment",
                    "sumcli verification-tests detach <attachment-id> --scope <scope> --confirm",
                ),
            ],
        )
    )


@app.command("list-attachments")
def list_attachments(
    ctx: typer.Context,
    scope: Annotated[Scope, typer.Option("--scope")],
    subject_type: Annotated[SubjectType, typer.Option("--subject-type")],
    project: ProjectOption = None,
    artifact: ArtifactOption = None,
    target_org: TargetOrgOption = None,
    profile: ProfileOption = None,
) -> None:
    target_org = _target_org(target_org)
    _, params = _scope_params(
        ctx,
        scope=scope,
        project=project,
        artifact=artifact,
        target_org=target_org,
        profile=profile,
    )
    params["subject_type"] = subject_type.value
    with api_client(ctx, profile) as client:
        response = client.request(
            "GET",
            "/v1/verification-tests/attachments",
            params=params,
            headers=_target_headers(target_org),
        )
    attachments = _required_list(response, expected="verification-test attachment list")
    emit(
        ok(
            {
                "attachments": attachments,
                "scope": params["scope"],
                "scope_id": params.get("scope_id"),
                "target_org": target_org,
            }
        )
    )


@app.command("detach")
def detach(
    ctx: typer.Context,
    attachment_id: Annotated[
        str, typer.Argument(help="Attachment id from list-attachments (vta-...).")
    ],
    scope: Annotated[Scope, typer.Option("--scope")],
    project: ProjectOption = None,
    artifact: ArtifactOption = None,
    target_org: TargetOrgOption = None,
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Confirm soft-detaching this attachment.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Emit the exact DELETE request; no confirmation or network."
        ),
    ] = False,
    profile: ProfileOption = None,
) -> None:
    target_org = _target_org(target_org)
    attachment_id = _path_segment(attachment_id, flag="ATTACHMENT_ID")
    _, params = _scope_params(
        ctx,
        scope=scope,
        project=project,
        artifact=artifact,
        target_org=target_org,
        profile=profile,
    )
    path = f"/v1/verification-tests/attachments/{attachment_id}"
    if dry_run:
        emit(
            ok(
                {
                    "dry_run": True,
                    "request": _request_preview(
                        "DELETE",
                        path,
                        params={**params, "confirm": True},
                        target_org=target_org,
                    ),
                }
            )
        )
        return
    require_confirm(confirm, action_name="verification-tests detach")
    with api_client(ctx, profile) as client:
        response = client.request(
            "DELETE",
            path,
            params={**params, "confirm": True},
            headers=_target_headers(target_org),
        )
    attachment = _required_dict(response, expected="detached verification-test attachment")
    emit(
        ok(
            {"detached": attachment_id, "attachment": attachment, "target_org": target_org},
            next_actions=[
                action(
                    "Preview the effective set",
                    "sumcli verification-tests preview --scope <scope> --subject-type <type>",
                ),
                action(
                    "List remaining attachments",
                    "sumcli verification-tests list-attachments --scope <scope> "
                    "--subject-type <type>",
                ),
            ],
        )
    )


@app.command("preview")
def preview(
    ctx: typer.Context,
    scope: Annotated[Scope, typer.Option("--scope")],
    subject_type: Annotated[SubjectType, typer.Option("--subject-type")],
    project: ProjectOption = None,
    artifact: ArtifactOption = None,
    target_org: TargetOrgOption = None,
    profile: ProfileOption = None,
) -> None:
    target_org = _target_org(target_org)
    _, params = _scope_params(
        ctx,
        scope=scope,
        project=project,
        artifact=artifact,
        target_org=target_org,
        profile=profile,
    )
    params["subject_type"] = subject_type.value
    with api_client(ctx, profile) as client:
        response = client.request(
            "GET",
            "/v1/verification-tests/effective",
            params=params,
            headers=_target_headers(target_org),
        )
    effective = _required_dict(response, expected="effective verification-test preview")
    emit(
        ok(
            {"preview": effective, "target_org": target_org},
            next_actions=[
                action(
                    "Add or remove an overlay",
                    "sumcli verification-tests attach --scope <scope> --subject-type "
                    "<type> --op <add-or-remove>",
                ),
                action(
                    "List attachment ids",
                    "sumcli verification-tests list-attachments --scope <scope> "
                    "--subject-type <type>",
                ),
            ],
        )
    )


assert tuple(item.value for item in SubjectType) == tuple(CUSTOM_TEST_SUBJECT_TYPES)
assert tuple(item.value for item in Scope) == _SCOPES
