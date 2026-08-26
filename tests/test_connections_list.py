"""connections list response parsing tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _run(return_value: object) -> tuple[object, MagicMock]:
    client = MagicMock()
    client.request.return_value = return_value
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None
    with patch("sum_cli.resources.connections.api_client", return_value=cm):
        result = runner.invoke(app, ["connections", "list"])
    return result, client


def test_list_unwraps_data_wrapper() -> None:
    result, _ = _run(
        {
            "data": {
                "connections": [{"id": "c1", "name": "fantasypros_nfl"}],
                "total": 1,
            }
        }
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["result"]["connections"][0]["id"] == "c1"
    assert body["result"]["total"] == 1


def test_list_reads_top_level_connections_without_data_wrapper() -> None:
    """Regression: some tenants return {connections, total} without a data key."""
    result, _ = _run(
        {
            "connections": [
                {"id": "c1", "name": "fantasypros_nfl"},
                {"id": "c2", "name": "sleeper_nfl"},
            ],
            "total": 2,
        }
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert [c["name"] for c in body["result"]["connections"]] == [
        "fantasypros_nfl",
        "sleeper_nfl",
    ]
    assert body["result"]["total"] == 2
