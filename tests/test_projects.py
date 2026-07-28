"""Projects command tests with mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.config_store import write_all
from sum_cli.cli.main import app

runner = CliRunner()


def test_projects_list_envelope(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.request.return_value = {"data": {"projects": [{"id": "proj_1", "name": "One"}]}}
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    with patch("sum_cli.resources.projects.api_client", return_value=mock_cm):
        result = runner.invoke(app, ["projects", "list"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["projects"][0]["id"] == "proj_1"


def test_projects_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://example.com",
                "default_project": "proj_1",
            }
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    mock_client = MagicMock()
    mock_client.request.return_value = {"data": {"id": "proj_1", "name": "One"}}
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.projects.api_client", return_value=mock_cm):
        result = runner.invoke(app, ["projects", "current"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["project"]["id"] == "proj_1"
