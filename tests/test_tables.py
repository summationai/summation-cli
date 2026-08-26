"""tables import stdout contract tests."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app, main
from sum_cli.client import ApiError

runner = CliRunner()


def _import_mocks(csv_path: Path) -> MagicMock:
    mock_client = MagicMock()

    def request(method: str, path: str, **kwargs: object) -> dict:
        if path == "/v1/assets/upload-urls":
            return {"data": {"assetId": "a1", "uploadUrl": "https://upload.example/x"}}
        if "previews" in path:
            return {"data": {"originalColumns": [{"name": "col1"}]}}
        if path == "/v1/table-imports" and method == "POST":
            return {"data": {"importRequestId": "imp1", "importStatus": "RUNNING"}}
        if path == "/v1/table-imports/imp1":
            return {"data": {"importRequestId": "imp1", "importStatus": "COMPLETED"}}
        if path == "/v1/tables" and method == "GET":
            return {"data": {"tables": [{"id": "tbl-t1", "tableName": "t1"}]}}
        return {}

    mock_client.request.side_effect = request
    mock_client.put_url.return_value = None
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_cm


def test_tables_import_wait_ends_with_ndjson_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n1,2\n")
    with patch(
        "sum_cli.resources.tables.api_client",
        return_value=_import_mocks(csv_file),
    ):
        with patch("sum_cli.resources.tables.time.sleep"):
            result = runner.invoke(
                app,
                ["tables", "import", "--local", "--path", str(csv_file), "--table", "t1", "--wait"],
            )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.strip().split("\n") if ln]
    assert len(lines) >= 2
    last = json.loads(lines[-1])
    assert last["type"] == "result"
    assert last["ok"] is True
    assert last["result"]["table_id"] == "tbl-t1"


def test_tables_import_no_wait_single_json_envelope(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n1,2\n")
    with patch(
        "sum_cli.resources.tables.api_client",
        return_value=_import_mocks(csv_file),
    ):
        result = runner.invoke(
            app,
            [
                "tables",
                "import",
                "--local",
                "--path",
                str(csv_file),
                "--table",
                "t1",
                "--no-wait",
            ],
        )
    assert result.exit_code == 0
    body = json.loads(result.stdout.strip())
    assert body["ok"] is True
    assert "import_id" in body["result"]
    assert body["result"]["table_id"] == "tbl-t1"


def _append_mock() -> tuple[MagicMock, MagicMock]:
    mock_client = MagicMock()
    mock_client.request.return_value = {"data": {"status": "FULL", "insertedRefIds": ["r1"]}}
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_cm, mock_client


def test_tables_append_rows_inline(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    mock_cm, mock_client = _append_mock()
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            [
                "tables",
                "append",
                "tbl-1",
                "--rows",
                '[{"campaign_id": "c1", "status": "accept"}]',
            ],
        )
    assert result.exit_code == 0
    body = json.loads(result.stdout.strip())
    assert body["ok"] is True
    method, path = mock_client.request.call_args.args[0], mock_client.request.call_args.args[1]
    assert method == "POST"
    assert path == "/v1/tables/tbl-1/rows"
    sent = mock_client.request.call_args.kwargs["json"]
    assert sent["rows"] == [{"campaign_id": "c1", "status": "accept"}]
    assert sent == {"rows": [{"campaign_id": "c1", "status": "accept"}]}


def test_tables_append_rows_from_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    rows_file = tmp_path / "rows.json"
    rows_file.write_text('[{"campaign_id": "c2"}]')
    mock_cm, mock_client = _append_mock()
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(app, ["tables", "append", "tbl-1", "--file", str(rows_file)])
    assert result.exit_code == 0
    sent = mock_client.request.call_args.kwargs["json"]
    assert sent == {"rows": [{"campaign_id": "c2"}]}


def test_tables_append_requires_one_source(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    result = runner.invoke(app, ["tables", "append", "tbl-1"])
    assert result.exit_code == 1
    body = json.loads(result.stdout.strip().split("\n")[-1])
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_FLAGS"


def test_tables_append_partial_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    mock_client = MagicMock()
    mock_client.request.return_value = {
        "data": {
            "status": "PARTIAL",
            "insertedRefIds": ["r1"],
            "errors": [
                {
                    "code": "DUPLICATE_RECORD",
                    "message": "Duplicate entry",
                    "refId": "r2",
                }
            ],
        }
    }
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["tables", "append", "tbl-1", "--rows", '[{"campaign_id": "c1"}]'],
        )
    assert result.exit_code == 1
    body = json.loads(result.stdout.strip())
    assert body["ok"] is False
    assert body["error"]["code"] == "APPEND_PARTIAL"
    assert body["error"]["data"]["errors"][0]["message"] == "Duplicate entry"


def test_tables_append_none_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    mock_client = MagicMock()
    mock_client.request.side_effect = ApiError(
        422,
        {
            "code": "append_failed",
            "detail": "No rows were appended: Column status cannot be null",
            "errors": [
                {
                    "code": "INVALID_DATA_ENTRY",
                    "message": "Column status cannot be null",
                    "refId": "r1",
                }
            ],
        },
        method="POST",
        url="https://example.com/v1/tables/tbl-1/rows",
    )
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    monkeypatch.setattr(
        sys,
        "argv",
        ["sumcli", "tables", "append", "tbl-1", "--rows", '[{"campaign_id": "c1"}]'],
    )
    buf = io.StringIO()
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                main()
    assert exc.value.code == 1
    body = json.loads(buf.getvalue())
    assert body["ok"] is False
    assert body["error"]["code"] == "append_failed"
    assert "No rows were appended" in body["error"]["message"]


def _upsert_mock() -> tuple[MagicMock, MagicMock]:
    mock_client = MagicMock()
    mock_client.request.return_value = {"data": {"inserted": 1, "updated": 0, "errors": []}}
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_cm, mock_client


def test_tables_upsert_rows_inline(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    mock_cm, mock_client = _upsert_mock()
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            [
                "tables",
                "upsert",
                "tbl-1",
                "--rows",
                '[{"event_id": "550e8400-e29b-41d4-a716-446655440000", "op": "x"}]',
            ],
        )
    assert result.exit_code == 0
    body = json.loads(result.stdout.strip())
    assert body["ok"] is True
    method, path = mock_client.request.call_args.args[0], mock_client.request.call_args.args[1]
    assert method == "PUT"
    assert path == "/v1/tables/tbl-1/rows"
    assert mock_client.request.call_args.kwargs["json"] == {
        "rows": [{"event_id": "550e8400-e29b-41d4-a716-446655440000", "op": "x"}]
    }


def test_tables_upsert_sends_key_columns(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    mock_cm, mock_client = _upsert_mock()
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            [
                "tables",
                "upsert",
                "tbl-1",
                "--rows",
                '[{"event_id": "a"}]',
                "--key-column",
                "event_id",
            ],
        )
    assert result.exit_code == 0
    sent = mock_client.request.call_args.kwargs["json"]
    assert sent["key_columns"] == ["event_id"]


def test_tables_upsert_rejects_s_id_in_rows(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    result = runner.invoke(
        app,
        ["tables", "upsert", "tbl-1", "--rows", '[{"s_id": 1, "event_id": "a"}]'],
    )
    assert result.exit_code == 1
    body = json.loads(result.stdout.strip())
    assert body["error"]["code"] == "INVALID_ROWS"


def test_tables_upsert_partial_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    mock_client = MagicMock()
    mock_client.request.return_value = {
        "data": {
            "inserted": 1,
            "updated": 0,
            "errors": [{"code": "INVALID_DATA_ENTRY", "message": "bad row"}],
        }
    }
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["tables", "upsert", "tbl-1", "--rows", '[{"event_id": "a"}]'],
        )
    assert result.exit_code == 1
    body = json.loads(result.stdout.strip())
    assert body["error"]["code"] == "UPSERT_PARTIAL"


def test_tables_import_wait_failed_status_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n1,2\n")

    mock_client = MagicMock()

    def request(method: str, path: str, **kwargs: object) -> dict:
        if path == "/v1/assets/upload-urls":
            return {"data": {"assetId": "a1", "uploadUrl": "https://upload.example/x"}}
        if "previews" in path:
            return {"data": {"originalColumns": [{"name": "col1"}]}}
        if path == "/v1/table-imports" and method == "POST":
            return {"data": {"importRequestId": "imp1", "importStatus": "RUNNING"}}
        if path == "/v1/table-imports/imp1":
            return {"data": {"importRequestId": "imp1", "importStatus": "FAILED"}}
        return {}

    mock_client.request.side_effect = request
    mock_client.put_url.return_value = None
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        with patch("sum_cli.resources.tables.time.sleep"):
            result = runner.invoke(
                app,
                ["tables", "import", "--local", "--path", str(csv_file), "--table", "t1", "--wait"],
            )

    assert result.exit_code == 1
    last = json.loads(result.stdout.strip().split("\n")[-1])
    assert last["type"] == "error"
    assert last["ok"] is False
    assert last["error"]["code"] == "IMPORT_FAILED"


def test_tables_import_refresh_sends_full_refresh_and_confirm(monkeypatch, tmp_path: Path) -> None:
    """--refresh replaces an existing table's rows: the API requires confirm=true
    for FULL_REFRESH, and the explicit flag is the user's confirmation."""
    csv = tmp_path / "data.csv"
    csv.write_text("col1\n1\n")
    mock_cm = _import_mocks(csv)
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "t")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            [
                "tables",
                "import",
                "--table",
                "t1",
                "--local",
                "--path",
                str(csv),
                "--no-wait",
                "--refresh",
            ],
        )
    assert result.exit_code == 0
    client = mock_cm.__enter__.return_value
    post = next(
        c
        for c in client.request.call_args_list
        if c.args[0] == "POST" and "table-imports" in c.args[1]
    )
    assert post.args[1] == "/v1/table-imports"
    assert post.kwargs["params"] == {"confirm": "true"}
    assert post.kwargs["json"]["import_type"] == "FULL_REFRESH"


def test_tables_import_default_is_new_without_confirm(monkeypatch, tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("col1\n1\n")
    mock_cm = _import_mocks(csv)
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "t")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    with patch("sum_cli.resources.tables.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["tables", "import", "--table", "t1", "--local", "--path", str(csv), "--no-wait"],
        )
    assert result.exit_code == 0
    client = mock_cm.__enter__.return_value
    post = next(
        c
        for c in client.request.call_args_list
        if c.args[0] == "POST" and "table-imports" in c.args[1]
    )
    assert post.args[1] == "/v1/table-imports"
    assert not post.kwargs.get("params")
    assert post.kwargs["json"]["import_type"] == "NEW"
