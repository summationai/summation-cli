"""Tests for `sumcli queries run` row extraction and auto-pagination."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.resources.queries import (
    _API_MAX_PAGE,
    _page_sql,
    _run_paginated,
    extract_query_rows,
)

runner = CliRunner()


def _api_env(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _row(n: int) -> dict:
    return {"columns": {"n": str(n)}}


def _page_body(rows: list[dict]) -> dict:
    return {"status": "succeeded", "result": {"rows": rows, "rowsWithColumnOrder": []}}


def test_extract_query_rows_nested() -> None:
    data = _page_body([_row(1), _row(2)])
    assert len(extract_query_rows(data)) == 2


def test_extract_query_rows_flat_fallback() -> None:
    assert extract_query_rows({"rows": [_row(1)]}) == [_row(1)]
    assert extract_query_rows({"results": [_row(1)]}) == [_row(1)]


def test_page_sql_offset() -> None:
    assert _page_sql("select 1;", 0) == "select 1"
    assert "OFFSET 10000" in _page_sql("select * from t", 10000)
    assert "_sumcli_page" in _page_sql("select * from t", 10000)


def test_single_page_under_cap() -> None:
    client = MagicMock()
    client.request.return_value = _page_body([_row(i) for i in range(5)])

    result = _run_paginated(client, "select 1", desired=5)

    assert result["showing"] == 5
    assert result["truncated"] is True  # full page at limit
    assert result["limit"] == 5
    assert "pages" not in result
    client.request.assert_called_once()
    payload = client.request.call_args.kwargs["json"]
    assert payload["limit"] == 5
    assert payload["sql"] == "select 1"


def test_exhausted_not_truncated() -> None:
    client = MagicMock()
    client.request.return_value = _page_body([_row(1), _row(2)])

    result = _run_paginated(client, "select 1", desired=100)

    assert result["showing"] == 2
    assert result["truncated"] is False


def test_auto_paginate_above_api_max() -> None:
    client = MagicMock()

    def _respond(_method, _path, json=None):
        limit = json["limit"]
        sql = json["sql"]
        if "OFFSET" not in sql:
            return _page_body([_row(i) for i in range(limit)])
        # Full final page at the remaining limit → may be more beyond --limit.
        return _page_body([_row(i) for i in range(_API_MAX_PAGE, _API_MAX_PAGE + limit)])

    client.request.side_effect = _respond

    desired = _API_MAX_PAGE + 50
    result = _run_paginated(client, "select * from t", desired=desired)

    assert client.request.call_count == 2
    assert result["showing"] == desired
    assert result["pages"] == 2
    assert result["truncated"] is True
    second_sql = client.request.call_args_list[1].kwargs["json"]["sql"]
    assert "OFFSET 10000" in second_sql


def test_auto_paginate_stops_when_source_exhausted() -> None:
    client = MagicMock()

    def _respond(_method, _path, json=None):
        sql = json["sql"]
        if "OFFSET" not in sql:
            return _page_body([_row(i) for i in range(_API_MAX_PAGE)])
        return _page_body([_row(i) for i in range(10)])  # short final page

    client.request.side_effect = _respond

    result = _run_paginated(client, "select * from t", desired=_API_MAX_PAGE + 5000)

    assert result["showing"] == _API_MAX_PAGE + 10
    assert result["pages"] == 2
    assert result["truncated"] is False


def test_cli_queries_run_extracts_nested_rows(monkeypatch) -> None:
    _api_env(monkeypatch)
    mock_client = MagicMock()
    mock_client.request.return_value = _page_body([_row(1), _row(2), _row(3)])
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.queries.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["queries", "run", "--sql", "select 1", "--limit", "10"],
        )

    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["showing"] == 3
    assert len(body["result"]["rows"]) == 3
    assert body["result"]["truncated"] is False
