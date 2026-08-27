from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.config_store import write_all

runner = CliRunner()
_VALID_YAML = Path(__file__).parent / "fixtures" / "custom-test-bundle-valid.yaml"

_BUNDLE = {
    "version": "custom-test-bundle/v1",
    "subject_type": "deck",
    "tests": [
        {
            "test_id": "brand_color",
            "category": "Visual",
            "name": "Brand color",
            "display_name": "Brand Color Compliance",
            "pass_criteria": "PASS if the approved brand palette is used.",
            "requiredness": "advisory",
            "render_dependent": False,
        }
    ],
}


def _write_bundle(tmp_path: Path, body: dict | str, *, suffix: str = ".yaml") -> Path:
    path = tmp_path / f"bundle{suffix}"
    if isinstance(body, str):
        path.write_text(body, encoding="utf-8")
    else:
        path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _mock_client(monkeypatch: pytest.MonkeyPatch, response: object) -> MagicMock:
    client = MagicMock()
    client.request.return_value = response
    manager = MagicMock()
    manager.__enter__.return_value = client
    manager.__exit__.return_value = None
    monkeypatch.setattr(
        "sum_cli.resources.verification_tests.api_client", MagicMock(return_value=manager)
    )
    return client


def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://api.example.test")


def test_validate_accepts_yaml_and_json_without_loading_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = _write_bundle(tmp_path, _BUNDLE, suffix=".json")
    monkeypatch.setattr(
        "sum_cli.resources.verification_tests.api_client",
        MagicMock(side_effect=AssertionError("offline validation attempted authentication")),
    )
    update_check = MagicMock(side_effect=AssertionError("offline validation attempted network"))
    monkeypatch.setattr("sum_cli.cli.main.warn_if_outdated", update_check)

    yaml_result = runner.invoke(
        app,
        ["verification-tests", "validate", "--bundle", str(_VALID_YAML)],
    )
    json_result = runner.invoke(app, ["verification-tests", "validate", "--bundle", str(json_path)])

    for result in (yaml_result, json_result):
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["result"]["valid"] is True
        assert payload["result"]["subject_type"] == "deck"
        assert payload["result"]["test_count"] == 1
    update_check.assert_not_called()


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda body: body["tests"][0].update({"test_id": "citation_accuracy"}), "test_id"),
        (lambda body: body["tests"][0].update({"test_id": "citations"}), "test_id"),
        (lambda body: body["tests"][0].update({"category": "   "}), "category"),
        (lambda body: body["tests"][0].update({"name": "   "}), "name"),
        (lambda body: body["tests"][0].update({"pass_criteria": "   "}), "pass_criteria"),
        (lambda body: body["tests"][0].update({"display_name": "   "}), "display_name"),
        (lambda body: body["tests"][0].update({"display_name": "x" * 201}), "display_name"),
        (lambda body: body["tests"][0].update({"pass_criteria": "x" * 2001}), "pass_criteria"),
        (lambda body: body["tests"][0].update({"verifier_ref": "python.evil:run"}), "verifier_ref"),
        (lambda body: body.update({"unknown": True}), "unknown"),
        (lambda body: body.update({"version": "custom-test-bundle/v2"}), "version"),
        (lambda body: body.update({"subject_type": "spreadsheet"}), "subject_type"),
        (lambda body: body.update({"tests": []}), "tests"),
        (lambda body: body["tests"].append(copy.deepcopy(body["tests"][0])), "test_id"),
        (lambda body: body["tests"][0].update({"test_id": "a" * 200}), "test_id"),
        (
            lambda body: body.update(
                {
                    "tests": [
                        {
                            **copy.deepcopy(body["tests"][0]),
                            "test_id": f"test_{index}",
                        }
                        for index in range(201)
                    ]
                }
            ),
            "tests",
        ),
    ],
)
def test_validate_ports_canonical_rejection_cases(
    tmp_path: Path,
    mutate: object,
    field: str,
) -> None:
    body = copy.deepcopy(_BUNDLE)
    mutate(body)  # type: ignore[operator]
    path = _write_bundle(tmp_path, body, suffix=".json")

    result = runner.invoke(app, ["verification-tests", "validate", "--bundle", str(path)])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "INVALID_BUNDLE"
    assert field in payload["error"]["message"]


def test_validate_rejects_category_cap(tmp_path: Path) -> None:
    body = copy.deepcopy(_BUNDLE)
    body["tests"] = [
        {
            **copy.deepcopy(_BUNDLE["tests"][0]),
            "test_id": f"test_{index}",
            "category": f"category_{index}",
        }
        for index in range(51)
    ]
    path = _write_bundle(tmp_path, body)

    result = runner.invoke(app, ["verification-tests", "validate", "--bundle", str(path)])

    assert result.exit_code == 1
    assert "categor" in json.loads(result.stdout)["error"]["message"].lower()


def test_validate_supports_standard_human_output(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, _BUNDLE)

    result = runner.invoke(
        app,
        ["--output", "human", "verification-tests", "validate", "--bundle", str(bundle)],
    )

    assert result.exit_code == 0, result.stdout
    assert "valid: yes" in result.stdout.lower()
    assert "subject type: deck" in result.stdout.lower()


def test_upload_maps_public_request_and_target_org(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api_env(monkeypatch)
    bundle = _write_bundle(tmp_path, _BUNDLE)
    client = _mock_client(
        monkeypatch,
        {
            "data": {
                "created": [
                    {
                        "id": "vtd-1",
                        "testId": "brand_color",
                        "displayName": "Brand Color Compliance",
                        "requiredness": "advisory",
                        "renderDependent": False,
                    }
                ]
            }
        },
    )

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "upload",
            "--bundle",
            str(bundle),
            "--target-org",
            "org-customer",
        ],
    )

    assert result.exit_code == 0, result.stdout
    client.request.assert_called_once_with(
        "POST",
        "/v1/verification-tests",
        json=_BUNDLE,
        headers={"x-agent-proxy-target-org": "org-customer"},
    )
    payload = json.loads(result.stdout)
    assert payload["result"]["created"][0]["id"] == "vtd-1"
    assert payload["next_actions"]


def test_upload_dry_run_emits_exact_request_without_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _write_bundle(tmp_path, _BUNDLE)
    offline = MagicMock(side_effect=AssertionError("dry run attempted authentication"))
    monkeypatch.setattr("sum_cli.resources.verification_tests.api_client", offline)

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "upload",
            "--bundle",
            str(bundle),
            "--target-org",
            "org-customer",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.stdout
    request = json.loads(result.stdout)["result"]["request"]
    assert request == {
        "method": "POST",
        "path": "/v1/verification-tests",
        "headers": {"x-agent-proxy-target-org": "org-customer"},
        "body": _BUNDLE,
    }
    offline.assert_not_called()


def test_upload_rejects_missing_created_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api_env(monkeypatch)
    bundle = _write_bundle(tmp_path, _BUNDLE)
    _mock_client(monkeypatch, {"data": {}})

    result = runner.invoke(
        app,
        ["verification-tests", "upload", "--bundle", str(bundle)],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "UNEXPECTED_SHAPE"


def test_list_maps_filters_count_and_public_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    _api_env(monkeypatch)
    client = _mock_client(
        monkeypatch,
        {
            "data": [{"id": "vtd-1", "testId": "brand_color"}],
            "showing": 1,
            "total": 3,
            "truncated": True,
        },
    )

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "list",
            "--subject-type",
            "deck",
            "--count",
            "1",
            "--target-org",
            "org-customer",
        ],
    )

    assert result.exit_code == 0, result.stdout
    client.request.assert_called_once_with(
        "GET",
        "/v1/verification-tests",
        params={"subject_type": "deck", "limit": 1},
        headers={"x-agent-proxy-target-org": "org-customer"},
    )
    payload = json.loads(result.stdout)["result"]
    assert payload == {
        "tests": [{"id": "vtd-1", "testId": "brand_color"}],
        "showing": 1,
        "total": 3,
        "truncated": True,
        "target_org": "org-customer",
    }


@pytest.mark.parametrize(
    ("args", "expected_body"),
    [
        (
            ["--op", "add", "--custom-test-id", "vtd-1"],
            {
                "scope": "project",
                "scope_id": "prj-1",
                "subject_type": "deck",
                "op": "add",
                "custom_test_id": "vtd-1",
            },
        ),
        (
            ["--op", "remove", "--target-ref", "deck_rubric:readability"],
            {
                "scope": "project",
                "scope_id": "prj-1",
                "subject_type": "deck",
                "op": "remove",
                "target_ref": "deck_rubric:readability",
            },
        ),
    ],
)
def test_attach_keeps_add_and_remove_overlays_distinct(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_body: dict[str, str],
) -> None:
    _api_env(monkeypatch)
    client = _mock_client(monkeypatch, {"data": {"id": "vta-1", "status": "active"}})

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "attach",
            "--scope",
            "project",
            "--project",
            "prj-1",
            "--subject-type",
            "deck",
            *args,
        ],
    )

    assert result.exit_code == 0, result.stdout
    client.request.assert_called_once_with(
        "POST",
        "/v1/verification-tests/attachments",
        json=expected_body,
        headers=None,
    )


def test_attach_project_uses_profile_default_but_cross_org_requires_explicit_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_file = tmp_path / "summation-config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://api.example.test",
                "access_token": "token",
                "default_project": "prj-default",
            }
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    client = _mock_client(monkeypatch, {"data": {"id": "vta-1", "status": "active"}})
    common = [
        "verification-tests",
        "attach",
        "--scope",
        "project",
        "--subject-type",
        "deck",
        "--op",
        "add",
        "--custom-test-id",
        "vtd-1",
    ]

    same_org = runner.invoke(app, common)
    blank_project = runner.invoke(app, [*common, "--project", ""])
    cross_org = runner.invoke(app, [*common, "--target-org", "org-customer"])

    assert same_org.exit_code == 0, same_org.stdout
    assert client.request.call_args.kwargs["json"]["scope_id"] == "prj-default"
    assert blank_project.exit_code == 1
    assert json.loads(blank_project.stdout)["error"]["code"] == "INVALID_REQUEST"
    assert cross_org.exit_code == 1
    error = json.loads(cross_org.stdout)
    assert error["error"]["code"] == "INVALID_REQUEST"
    assert "--project" in error["fix"]
    assert client.request.call_count == 1


def test_attach_project_uses_the_action_profile_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_file = tmp_path / "summation-config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://api.default.test",
                "access_token": "default-token",
                "default_project": "prj-default",
            },
            "customer": {
                "base_url": "https://api.customer.test",
                "access_token": "customer-token",
                "default_project": "prj-customer",
            },
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    client = _mock_client(monkeypatch, {"data": {"id": "vta-1", "status": "active"}})

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "attach",
            "--scope",
            "project",
            "--subject-type",
            "deck",
            "--op",
            "add",
            "--custom-test-id",
            "vtd-1",
            "--profile",
            "customer",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.kwargs["json"]["scope_id"] == "prj-customer"


@pytest.mark.parametrize(
    "args",
    [
        ["--scope", "tenant", "--project", "prj-1"],
        ["--scope", "project", "--artifact", "file-1"],
        ["--scope", "artifact", "--project", "prj-1"],
        ["--scope", "playbook"],
    ],
)
def test_scope_flag_mismatches_fail_before_network(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    offline = MagicMock(side_effect=AssertionError("invalid scope attempted network"))
    monkeypatch.setattr("sum_cli.resources.verification_tests.api_client", offline)

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "attach",
            *args,
            "--subject-type",
            "deck",
            "--op",
            "add",
            "--custom-test-id",
            "vtd-1",
        ],
    )

    assert result.exit_code != 0
    offline.assert_not_called()


def test_list_attachments_and_preview_map_scope_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    _api_env(monkeypatch)
    client = _mock_client(monkeypatch, {"data": []})
    client.request.side_effect = [
        {"data": []},
        {
            "data": {
                "scope": "artifact",
                "scopeId": "file-1",
                "subjectType": "workbook",
                "effective": [],
                "provenance": [],
            }
        },
    ]

    listed = runner.invoke(
        app,
        [
            "verification-tests",
            "list-attachments",
            "--scope",
            "artifact",
            "--artifact",
            "file-1",
            "--subject-type",
            "workbook",
        ],
    )
    preview = runner.invoke(
        app,
        [
            "verification-tests",
            "preview",
            "--scope",
            "artifact",
            "--artifact",
            "file-1",
            "--subject-type",
            "workbook",
        ],
    )

    assert listed.exit_code == 0, listed.stdout
    assert preview.exit_code == 0, preview.stdout
    assert client.request.call_args_list[0].args == ("GET", "/v1/verification-tests/attachments")
    assert client.request.call_args_list[0].kwargs["params"] == {
        "scope": "artifact",
        "scope_id": "file-1",
        "subject_type": "workbook",
    }
    assert client.request.call_args_list[1].args == ("GET", "/v1/verification-tests/effective")


def test_scoped_commands_apply_target_org_to_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api_env(monkeypatch)
    client = _mock_client(monkeypatch, {})
    client.request.side_effect = [
        {"data": {"id": "vta-add", "status": "active"}},
        {"data": []},
        {
            "data": {
                "scope": "tenant",
                "scopeId": "org-customer",
                "subjectType": "deck",
                "effective": [],
                "provenance": [],
            }
        },
        {"data": {"id": "vta-remove", "status": "deleted"}},
    ]
    target = ["--target-org", "org-customer"]

    attached = runner.invoke(
        app,
        [
            "verification-tests",
            "attach",
            "--scope",
            "project",
            "--project",
            "prj-customer",
            "--subject-type",
            "deck",
            "--op",
            "add",
            "--custom-test-id",
            "vtd-1",
            *target,
        ],
    )
    listed = runner.invoke(
        app,
        [
            "verification-tests",
            "list-attachments",
            "--scope",
            "artifact",
            "--artifact",
            "file-customer",
            "--subject-type",
            "deck",
            *target,
        ],
    )
    previewed = runner.invoke(
        app,
        [
            "verification-tests",
            "preview",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
            *target,
        ],
    )
    detached = runner.invoke(
        app,
        [
            "verification-tests",
            "detach",
            "vta-remove",
            "--scope",
            "artifact",
            "--artifact",
            "file-customer",
            "--confirm",
            *target,
        ],
    )

    for result in (attached, listed, previewed, detached):
        assert result.exit_code == 0, result.stdout
    assert [call.args[:2] for call in client.request.call_args_list] == [
        ("POST", "/v1/verification-tests/attachments"),
        ("GET", "/v1/verification-tests/attachments"),
        ("GET", "/v1/verification-tests/effective"),
        ("DELETE", "/v1/verification-tests/attachments/vta-remove"),
    ]
    for call in client.request.call_args_list:
        assert call.kwargs["headers"] == {"x-agent-proxy-target-org": "org-customer"}


@pytest.mark.parametrize("response_data", [{}, {"detail": "not a list"}])
def test_list_attachments_rejects_shape_drift(
    monkeypatch: pytest.MonkeyPatch,
    response_data: dict[str, object],
) -> None:
    _api_env(monkeypatch)
    _mock_client(monkeypatch, {"data": response_data})

    result = runner.invoke(
        app,
        [
            "verification-tests",
            "list-attachments",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "UNEXPECTED_SHAPE"


def test_list_definitions_rejects_empty_object_shape_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    _api_env(monkeypatch)
    _mock_client(monkeypatch, {"data": {}})

    result = runner.invoke(app, ["verification-tests", "list"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "UNEXPECTED_SHAPE"


@pytest.mark.parametrize(
    "response",
    [
        {"data": []},
        {"data": [], "showing": "0", "total": 0, "truncated": False},
        {"data": [], "showing": 0, "total": 0, "truncated": 0},
    ],
)
def test_list_definitions_requires_valid_pagination_metadata(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
) -> None:
    _api_env(monkeypatch)
    _mock_client(monkeypatch, response)

    result = runner.invoke(app, ["verification-tests", "list"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "UNEXPECTED_SHAPE"


def test_detach_requires_confirm_but_dry_run_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api_env(monkeypatch)
    client = _mock_client(monkeypatch, {"data": {"id": "vta-1", "status": "deleted"}})
    base = [
        "verification-tests",
        "detach",
        "vta-1",
        "--scope",
        "project",
        "--project",
        "prj-1",
    ]

    refused = runner.invoke(app, base)
    dry_run = runner.invoke(app, [*base, "--dry-run"])
    sent = runner.invoke(app, [*base, "--confirm"])

    assert refused.exit_code == 1
    assert json.loads(refused.stdout)["error"]["code"] == "CONFIRM_REQUIRED"
    assert dry_run.exit_code == 0
    request = json.loads(dry_run.stdout)["result"]["request"]
    assert request == {
        "method": "DELETE",
        "path": "/v1/verification-tests/attachments/vta-1",
        "query": {"scope": "project", "scope_id": "prj-1", "confirm": True},
        "headers": {},
    }
    assert sent.exit_code == 0, sent.stdout
    client.request.assert_called_once_with(
        "DELETE",
        "/v1/verification-tests/attachments/vta-1",
        params={"scope": "project", "scope_id": "prj-1", "confirm": True},
        headers=None,
    )


def test_attach_dry_run_validates_operation_and_never_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offline = MagicMock(side_effect=AssertionError("dry run attempted authentication"))
    monkeypatch.setattr("sum_cli.resources.verification_tests.api_client", offline)

    invalid = runner.invoke(
        app,
        [
            "verification-tests",
            "attach",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
            "--op",
            "add",
            "--target-ref",
            "x",
            "--dry-run",
        ],
    )
    valid = runner.invoke(
        app,
        [
            "verification-tests",
            "attach",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
            "--op",
            "remove",
            "--target-ref",
            "deck_rubric:readability",
            "--dry-run",
        ],
    )

    assert invalid.exit_code == 1
    assert valid.exit_code == 0, valid.stdout
    request = json.loads(valid.stdout)["result"]["request"]
    assert request["body"] == {
        "scope": "tenant",
        "subject_type": "deck",
        "op": "remove",
        "target_ref": "deck_rubric:readability",
    }
    offline.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["upload", "--target-org", "../customer"],
        ["upload", "--target-org", "org%3Fquery"],
        [
            "attach",
            "--scope",
            "project",
            "--project",
            "../other",
            "--subject-type",
            "deck",
            "--op",
            "add",
            "--custom-test-id",
            "vtd-1",
        ],
        [
            "attach",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
            "--op",
            "add",
            "--custom-test-id",
            "   ",
        ],
        [
            "attach",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
            "--op",
            "remove",
            "--target-ref",
            "   ",
        ],
        [
            "attach",
            "--scope",
            "tenant",
            "--subject-type",
            "deck",
            "--op",
            "remove",
            "--target-ref",
            "x" * 256,
        ],
        ["detach", "../vta-1", "--scope", "tenant"],
        ["detach", "vta%23fragment", "--scope", "tenant"],
    ],
)
def test_mutation_dry_runs_reject_invalid_ids_before_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    offline = MagicMock(side_effect=AssertionError("invalid dry run attempted authentication"))
    monkeypatch.setattr("sum_cli.resources.verification_tests.api_client", offline)
    bundle = _write_bundle(tmp_path, _BUNDLE)
    command = ["verification-tests", *args]
    if args[0] == "upload":
        command.extend(["--bundle", str(bundle)])
    command.append("--dry-run")

    result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    offline.assert_not_called()


def test_verification_tests_help_distinguishes_remove_overlay_from_detach() -> None:
    group = runner.invoke(app, ["verification-tests", "--help"])
    attach = runner.invoke(app, ["verification-tests", "attach", "--help"])
    detach = runner.invoke(app, ["verification-tests", "detach", "--help"])

    assert group.exit_code == attach.exit_code == detach.exit_code == 0
    assert "removal overlay" in attach.stdout.lower()
    assert "attachment" in detach.stdout.lower()
    assert "--confirm" in detach.stdout
