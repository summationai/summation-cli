"""Workflows command tests with mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _mock_client(return_value: object = None) -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.request.return_value = return_value
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None
    return client, cm


def _run(args: list[str], return_value: object = None) -> tuple[object, MagicMock]:
    client, cm = _mock_client(return_value)
    with patch("sum_cli.resources.workflows.api_client", return_value=cm):
        result = runner.invoke(app, args)
    return result, client


def test_list_sends_filters_and_page() -> None:
    result, client = _run(
        [
            "workflows",
            "list",
            "--project",
            "proj_1",
            "--page-token",
            "tok",
            "--page-size",
            "10",
        ],
        {"data": {"workflows": [{"id": "wf_1"}], "nextPageToken": "next"}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["workflows"][0]["id"] == "wf_1"
    assert body["result"]["next_page_token"] == "next"
    assert client.request.call_args[0] == ("GET", "/v1/workflows")
    assert client.request.call_args[1]["params"] == {
        "project_id": "proj_1",
        "page_token": "tok",
        "page_size": 10,
    }


def test_show_unwraps_data() -> None:
    result, client = _run(["workflows", "show", "wf_1"], {"data": {"id": "wf_1", "title": "T"}})
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["workflow"]["id"] == "wf_1"
    assert client.request.call_args[0] == ("GET", "/v1/workflows/wf_1")


def test_create_builds_payload_from_files(tmp_path: Path) -> None:
    graph = tmp_path / "graph.json"
    triggers = tmp_path / "triggers.json"
    graph.write_text(json.dumps({"nodes": [{"key": "t"}], "edges": []}))
    triggers.write_text(
        json.dumps(
            [
                {
                    "type": "schedule",
                    "label": "daily",
                    "schedule": {"type": "daily", "zone_id": "UTC"},
                }
            ]
        )
    )
    result, client = _run(
        [
            "workflows",
            "create",
            "--project",
            "proj_1",
            "--title",
            "Weekly",
            "--description",
            "desc",
            "--status",
            "draft",
            "--output-folder",
            "/Reports",
            "--graph-file",
            str(graph),
            "--triggers-file",
            str(triggers),
        ],
        {"data": {"id": "wf_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("POST", "/v1/workflows")
    payload = client.request.call_args[1]["json"]
    assert payload["project_id"] == "proj_1"
    assert payload["title"] == "Weekly"
    assert payload["description"] == "desc"
    assert payload["status"] == "draft"
    assert payload["output_folder"] == "/Reports"
    assert payload["graph"] == {"nodes": [{"key": "t"}], "edges": []}
    assert payload["triggers"][0]["schedule"]["zone_id"] == "UTC"


def test_create_rejects_active_status() -> None:
    result, client = _run(
        ["workflows", "create", "--project", "proj_1", "--title", "T", "--status", "active"]
    )
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_update_merges_existing_triggers_and_requires_revision() -> None:
    existing = {
        "data": {
            "id": "wf_1",
            "projectId": "proj_1",
            "title": "Old",
            "description": "Quarterly board deck summary",
            "outputFolder": "/Reports",
            "triggers": [{"id": "tr_1", "type": "schedule"}],
            "revision": 3,
        }
    }
    result, client = _run(
        [
            "workflows",
            "update",
            "wf_1",
            "--expected-revision",
            "3",
            "--title",
            "New",
        ],
        existing,
    )
    assert result.exit_code == 0, result.stdout
    # GET then PUT
    assert client.request.call_args_list[0][0] == ("GET", "/v1/workflows/wf_1")
    assert client.request.call_args_list[1][0] == ("PUT", "/v1/workflows/wf_1")
    payload = client.request.call_args_list[1][1]["json"]
    assert payload["title"] == "New"
    assert payload["project_id"] == "proj_1"
    assert payload["expected_revision"] == 3
    assert payload["triggers"] == [{"id": "tr_1", "type": "schedule"}]
    # Fields with default "" must be carried over or a rename resets them.
    assert payload["description"] == "Quarterly board deck summary"
    assert payload["output_folder"] == "/Reports"
    assert "graph" not in payload
    assert "status" not in payload


def test_update_omits_triggers_when_get_has_no_triggers_key() -> None:
    """Missing triggers must not become [] — that deletes every schedule."""
    existing = {
        "data": {
            "id": "wf_1",
            "projectId": "proj_1",
            "title": "Old",
            "revision": 3,
        }
    }
    result, client = _run(
        ["workflows", "update", "wf_1", "--expected-revision", "3", "--title", "New"],
        existing,
    )
    assert result.exit_code == 0, result.stdout
    payload = client.request.call_args_list[1][1]["json"]
    assert "triggers" not in payload


def test_update_refuses_empty_get_rather_than_default_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty GET must not PUT the profile default project_id onto the workflow."""
    from sum_cli.config_store import write_all

    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://example.com",
                "access_token": "test-token",
                "default_project": "prj-default-unrelated",
            }
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result, client = _run(
        ["workflows", "update", "wf_1", "--expected-revision", "3", "--title", "New"],
        None,
    )
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_REQUEST"
    # GET was attempted; PUT must not run with the unrelated default project.
    assert client.request.call_count == 1
    assert client.request.call_args[0] == ("GET", "/v1/workflows/wf_1")


def test_update_rejects_bad_graph_file_before_http(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    result, client = _run(
        [
            "workflows",
            "update",
            "wf_1",
            "--expected-revision",
            "1",
            "--graph-file",
            str(missing),
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_update_body_file_round_trip(tmp_path: Path) -> None:
    body_path = tmp_path / "wf.json"
    body_path.write_text(
        json.dumps(
            {
                "data": {
                    "id": "wf_1",
                    "projectId": "proj_1",
                    "title": "FromShow",
                    "description": "keep",
                    "status": "draft",
                    "outputFolder": "/Board",
                    "graph": {"nodes": [], "edges": []},
                    "triggers": [{"id": "tr_1"}],
                    "revision": 2,
                }
            }
        )
    )
    result, client = _run(
        [
            "workflows",
            "update",
            "wf_1",
            "--expected-revision",
            "2",
            "--body-file",
            str(body_path),
        ],
        {"data": {"id": "wf_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("PUT", "/v1/workflows/wf_1")
    payload = client.request.call_args[1]["json"]
    assert payload["title"] == "FromShow"
    assert payload["description"] == "keep"
    assert payload["status"] == "draft"
    assert payload["output_folder"] == "/Board"
    assert payload["graph"] == {"nodes": [], "edges": []}
    assert payload["triggers"] == [{"id": "tr_1"}]
    assert payload["expected_revision"] == 2


def test_activate_requires_confirm() -> None:
    result, client = _run(["workflows", "activate", "wf_1", "--expected-revision", "1"])
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_activate_sends_revision() -> None:
    result, client = _run(
        ["workflows", "activate", "wf_1", "--expected-revision", "4", "--confirm"],
        {"data": {"workflow": {"id": "wf_1"}, "version": {"id": "ver_1"}}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("POST", "/v1/workflows/wf_1/activate")
    assert client.request.call_args[1]["json"] == {"expected_revision": 4}


def test_versions_and_runs_and_run_show() -> None:
    result, client = _run(
        ["workflows", "versions", "wf_1", "--page-token", "p"],
        {"data": {"versions": [{"id": "v1"}], "next_page_token": "n"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("GET", "/v1/workflows/wf_1/versions")
    assert client.request.call_args[1]["params"] == {"page_token": "p"}

    result, client = _run(
        ["workflows", "runs", "wf_1", "--page-size", "7"],
        {"data": {"runs": [{"id": "r1"}]}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("GET", "/v1/workflows/wf_1/runs")
    assert client.request.call_args[1]["params"]["page_size"] == 7

    result, client = _run(
        ["workflows", "run-show", "wf_1", "run_1"],
        {"data": {"id": "run_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("GET", "/v1/workflows/wf_1/runs/run_1")


def test_run_requires_confirm() -> None:
    result, client = _run(["workflows", "run", "wf_1", "--version", "ver_1"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_run_generates_request_id_and_passes_version() -> None:
    result, client = _run(
        ["workflows", "run", "wf_1", "--version", "ver_1", "--confirm"],
        {"data": {"id": "run_1"}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["result"]["request_id"]
    assert body["result"]["workflow_version_id"] == "ver_1"
    assert client.request.call_args[0] == ("POST", "/v1/workflows/wf_1/runs")
    payload = client.request.call_args[1]["json"]
    assert payload["workflow_version_id"] == "ver_1"
    assert payload["request_id"] == body["result"]["request_id"]


def test_run_fetches_active_version_when_omitted() -> None:
    client, cm = _mock_client()
    client.request.side_effect = [
        {"data": {"id": "wf_1", "activeVersionId": "ver_active"}},
        {"data": {"id": "run_1"}},
    ]
    with patch("sum_cli.resources.workflows.api_client", return_value=cm):
        result = runner.invoke(app, ["workflows", "run", "wf_1", "--confirm"])
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args_list[0][0] == ("GET", "/v1/workflows/wf_1")
    assert client.request.call_args_list[1][1]["json"]["workflow_version_id"] == "ver_active"


def test_run_rejects_bad_request_id() -> None:
    result, client = _run(
        ["workflows", "run", "wf_1", "--version", "ver_1", "--request-id", "nope", "--confirm"]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_node_types() -> None:
    result, client = _run(
        ["workflows", "node-types"],
        {"data": {"nodeTypes": [{"id": "summation.playbook/v1"}]}},
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["node_types"][0]["id"] == "summation.playbook/v1"
    assert client.request.call_args[0] == ("GET", "/v1/workflows/node-types")
