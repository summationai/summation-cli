"""Files list response parsing tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.resources.files import _files_from_list_response

runner = CliRunner()


def test_files_from_directory_list_entries() -> None:
    data = {"path": "/", "entries": [{"id": "file-1", "file_name": "a.csv"}]}
    assert len(_files_from_list_response(data)) == 1


def test_files_from_search_groups() -> None:
    data = {
        "query": "report",
        "groups": [
            {"kind": "file", "results": [{"id": "file-1"}], "total": 1},
        ],
    }
    assert len(_files_from_list_response(data)) == 1


def test_files_list_cli_uses_entries(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    body = {"data": {"path": "/", "entries": [{"id": "file-abc", "file_name": "x.txt"}]}}
    mock_client = MagicMock()
    mock_client.request.return_value = body
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.files.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["files", "list", "--project", "prj-6qEdHxrzF2x0M2K"],
        )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["result"]["showing"] == 1
    assert out["result"]["files"][0]["id"] == "file-abc"
