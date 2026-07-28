"""CLI error envelope integration tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()


def test_files_list_no_project(monkeypatch, tmp_path) -> None:
    cfg_file = tmp_path / "config"
    cfg_file.write_text("")
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUMMATION_PROJECT", raising=False)
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    result = runner.invoke(app, ["files", "list"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "NO_PROJECT"
    assert "set-project" in body["fix"] or "--project" in body["fix"]
    assert len(body["next_actions"]) >= 1


def test_files_delete_forwards_recursive(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.request.return_value = None
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    with patch("sum_cli.resources.files.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["files", "delete", "file_1", "--project", "proj_1", "--confirm"],
        )

    assert result.exit_code == 0
    mock_client.request.assert_called_once_with(
        "DELETE",
        "/v1/projects/proj_1/files/file_1",
        params={"recursive": True, "confirm": True},
    )


def test_connections_delete_requires_confirm(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    result = runner.invoke(app, ["connections", "delete", "conn_1"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CONFIRM_REQUIRED"


def test_queries_run_requires_sql_or_file(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    result = runner.invoke(app, ["queries", "run"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_REQUEST"
