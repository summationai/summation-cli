"""Tests for `sumcli queries run` row extraction and auto-pagination."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.client import ApiError
from sum_cli.resources.queries import (
    _API_MAX_PAGE,
    _execute_query,
    _extract_query_rows,
    _page_sql,
    _run_paginated,
)

runner = CliRunner()


def _api_env(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _row(n: int) -> dict:
    return {"columns": {"n": str(n)}}


def _page_body(rows: list[dict], *, status: str = "succeeded") -> dict:
    return {"status": status, "result": {"rows": rows, "rowsWithColumnOrder": []}}


def test_extract_query_rows_nested() -> None:
    data = _page_body([_row(1), _row(2)])
    assert len(_extract_query_rows(data)) == 2


def test_extract_query_rows_flat_fallback() -> None:
    assert _extract_query_rows({"rows": [_row(1)]}) == [_row(1)]
    assert _extract_query_rows({"results": [_row(1)]}) == [_row(1)]


def test_extract_query_rows_rejects_non_dict() -> None:
    assert _extract_query_rows([_row(1)]) == []
    assert _extract_query_rows("nope") == []


def test_execute_query_normalizes_list_payload() -> None:
    client = MagicMock()
    client.request.return_value = [_row(1), _row(2)]
    data = _execute_query(client, "select 1", 10)
    assert data == {"rows": [_row(1), _row(2)]}
    assert _extract_query_rows(data) == [_row(1), _row(2)]


def test_page_sql_offset() -> None:
    assert _page_sql("select 1;", 0, 50) == "select 1"
    wrapped = _page_sql("select * from t", 10000, 50)
    assert "LIMIT 50 OFFSET 10000" in wrapped
    assert "_sumcli_page" in wrapped


def test_single_page_under_cap() -> None:
    client = MagicMock()
    client.request.return_value = _page_body([_row(i) for i in range(5)])

    result = _run_paginated(client, "select 1", desired=5)

    assert result["showing"] == 5
    assert result["truncated"] is True  # full page at limit → more may exist
    assert result["limit"] == 5
    assert result["pages"] == 1
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
    assert result["pages"] == 1


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
    assert "LIMIT 50 OFFSET 10000" in second_sql


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


def test_mid_pagination_api_error_emits_query_failed() -> None:
    """Live API failures are non-200 → ApiError; surface with page context."""
    client = MagicMock()

    def _respond(_method, _path, json=None):
        sql = json["sql"]
        if "OFFSET" not in sql:
            return _page_body([_row(i) for i in range(_API_MAX_PAGE)])
        raise ApiError(
            400,
            {
                "error": {
                    "code": "query_failed",
                    "message": "relation does not exist",
                }
            },
            method="POST",
            url="https://example.com/v1/query-executions",
        )

    client.request.side_effect = _respond

    with pytest.raises(SystemExit) as exc:
        _run_paginated(client, "select * from t", desired=_API_MAX_PAGE + 50)

    assert exc.value.code == 1


def test_mid_pagination_status_failed_defense_in_depth() -> None:
    """Keep status=failed handling if a 200 ever carries an application failure."""
    client = MagicMock()

    def _respond(_method, _path, json=None):
        sql = json["sql"]
        if "OFFSET" not in sql:
            return _page_body([_row(i) for i in range(_API_MAX_PAGE)])
        return {
            "status": "failed",
            "error": "timeout",
            "result": {"rows": []},
        }

    client.request.side_effect = _respond

    with pytest.raises(SystemExit) as exc:
        _run_paginated(client, "select * from t", desired=_API_MAX_PAGE + 50)

    assert exc.value.code == 1


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
    assert body["result"]["pages"] == 1


def test_cli_mid_pagination_api_error_exit_code(monkeypatch) -> None:
    _api_env(monkeypatch)
    mock_client = MagicMock()

    def _respond(_method, _path, json=None):
        sql = json["sql"]
        if "OFFSET" not in sql:
            return _page_body([_row(i) for i in range(_API_MAX_PAGE)])
        raise ApiError(
            400,
            {"error": {"code": "query_failed", "message": "timeout"}},
            method="POST",
            url="https://example.com/v1/query-executions",
        )

    mock_client.request.side_effect = _respond
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.queries.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            [
                "queries",
                "run",
                "--sql",
                "select * from t",
                "--limit",
                str(_API_MAX_PAGE + 50),
            ],
        )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "QUERY_FAILED"
    assert "timeout" in body["error"]["message"]
    assert body["error"]["data"]["rows_so_far"] == _API_MAX_PAGE
    assert body["error"]["data"]["page"] == 2
    assert body["error"]["data"]["offset"] == _API_MAX_PAGE
    assert body["error"]["data"]["query"]["http_status"] == 400
