"""`sumcli grid create` payload and validation tests with mocked HTTP.

Covers both kinds of POST /v1/grid/tables. The client-side checks mirror the API's
own validator, so a wrong schema is refused before a round trip; each test below
pins one refusal so a relaxed check cannot pass silently.
"""

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


def _run(args: list[str], return_value: object = None) -> tuple[object, MagicMock]:
    client = MagicMock()
    client.request.return_value = return_value or {"data": {"id": "tbl-1"}}
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None
    with patch("sum_cli.resources.grid.api_client", return_value=cm):
        result = runner.invoke(app, args)
    return result, client


def _sent_body(client: MagicMock) -> dict:
    method, path = client.request.call_args.args
    assert (method, path) == ("POST", "/v1/grid/tables")
    return client.request.call_args.kwargs["json"]


def _error(result: object) -> dict:
    return json.loads(result.stdout.strip().split("\n")[-1])


def test_calc_is_the_default_kind_and_sends_the_query() -> None:
    result, client = _run(["grid", "create", "t", "--query", "SELECT 1 AS n"])
    assert result.exit_code == 0
    assert _sent_body(client) == {"name": "t", "kind": "calc", "query": "SELECT 1 AS n"}


def test_data_kind_sends_columns_in_declared_order() -> None:
    result, client = _run(
        [
            "grid",
            "create",
            "ops_log",
            "--kind",
            "data",
            "--column",
            "event_id:uuid:notnull",
            "--column",
            "op:string",
            "--column",
            "count:integer",
        ]
    )
    assert result.exit_code == 0
    body = _sent_body(client)
    assert body["name"] == "ops_log"
    assert body["kind"] == "data"
    assert body["columns"] == [
        {"name": "event_id", "type": "uuid", "nullable": False},
        {"name": "op", "type": "string", "nullable": True},
        {"name": "count", "type": "integer", "nullable": True},
    ]
    # Absent rather than empty: the API treats an omitted key list as "no business key".
    assert "key_columns" not in body


def test_key_columns_take_the_declared_spelling() -> None:
    """The API matches key columns case-sensitively, so the caller's casing is rewritten."""
    result, client = _run(
        [
            "grid",
            "create",
            "t",
            "--kind",
            "data",
            "--column",
            "EventId:uuid",
            "--key-column",
            "eventid",
        ]
    )
    assert result.exit_code == 0
    assert _sent_body(client)["key_columns"] == ["EventId"]


def test_repeated_key_column_is_sent_once() -> None:
    result, client = _run(
        [
            "grid",
            "create",
            "t",
            "--kind",
            "data",
            "--column",
            "a:string",
            "--key-column",
            "a",
            "--key-column",
            "A",
        ]
    )
    assert result.exit_code == 0
    assert _sent_body(client)["key_columns"] == ["a"]


def test_columns_file_is_read_as_a_json_array(tmp_path: Path) -> None:
    path = tmp_path / "cols.json"
    path.write_text(json.dumps([{"name": "id", "type": "UUID", "nullable": False}]))
    result, client = _run(["grid", "create", "t", "--kind", "data", "--columns-file", str(path)])
    assert result.exit_code == 0
    # Type is normalized to the lowercase spelling the API's enum uses.
    assert _sent_body(client)["columns"] == [{"name": "id", "type": "uuid", "nullable": False}]


def test_columns_file_defaults_nullable_to_true(tmp_path: Path) -> None:
    path = tmp_path / "cols.json"
    path.write_text(json.dumps([{"name": "note", "type": "string"}]))
    result, client = _run(["grid", "create", "t", "--kind", "data", "--columns-file", str(path)])
    assert result.exit_code == 0
    assert _sent_body(client)["columns"][0]["nullable"] is True


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (["grid", "create", "t"], "--query is required with --kind calc"),
        (
            ["grid", "create", "t", "--query", "SELECT 1", "--column", "a:string"],
            "not allowed with --kind calc",
        ),
        (
            ["grid", "create", "t", "--query", "SELECT 1", "--key-column", "a"],
            "--key-column is not allowed with --kind calc",
        ),
        (["grid", "create", "t", "--kind", "data"], "needs at least one column"),
        (
            ["grid", "create", "t", "--kind", "data", "--query", "SELECT 1"],
            "--query is not allowed with --kind data",
        ),
        (["grid", "create", "t", "--kind", "table"], "is not calc or data"),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "a"],
            "is not name:type",
        ),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "a:varchar"],
            "unknown type",
        ),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "a:string:maybe"],
            "unknown nullability",
        ),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "s_id:integer"],
            "belongs to the table's row store",
        ),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "s_created_at:datetime"],
            "belongs to the table's row store",
        ),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "_sm_created_at:datetime"],
            "belongs to the table's row store",
        ),
        (
            [
                "grid",
                "create",
                "t",
                "--kind",
                "data",
                "--column",
                "a:string",
                "--column",
                "A:integer",
            ],
            "Duplicate column name",
        ),
        (
            ["grid", "create", "t", "--kind", "data", "--column", "a:string", "--key-column", "b"],
            "is not a declared column",
        ),
    ],
)
def test_invalid_input_is_refused_before_any_request(args: list[str], fragment: str) -> None:
    result, client = _run(args)
    assert result.exit_code != 0
    assert fragment in _error(result)["error"]["message"]
    client.request.assert_not_called()


def test_empty_query_is_refused_under_kind_data() -> None:
    """An explicitly-typed --query "" must be reported, not silently dropped.

    Truthiness would treat it as absent and build a data payload as though the flag were
    never passed, leaving "why was my query ignored?" unanswered.
    """
    result, client = _run(
        ["grid", "create", "t", "--kind", "data", "--query", "", "--column", "a:string"]
    )
    assert result.exit_code != 0
    assert "--query is not allowed with --kind data" in _error(result)["error"]["message"]
    client.request.assert_not_called()


def test_duplicate_columns_are_refused_before_keys_resolve() -> None:
    """The duplicate check must run before key resolution, which is case-insensitive.

    Were the order reversed, --key-column a and A would collapse to one key and the table
    would be created with a business key the caller did not ask for.
    """
    result, client = _run(
        [
            "grid",
            "create",
            "t",
            "--kind",
            "data",
            "--column",
            "a:string",
            "--column",
            "A:integer",
            "--key-column",
            "a",
            "--key-column",
            "A",
        ]
    )
    assert result.exit_code != 0
    assert "Duplicate column name" in _error(result)["error"]["message"]
    client.request.assert_not_called()


def test_column_and_columns_file_together_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "cols.json"
    path.write_text(json.dumps([{"name": "a", "type": "string"}]))
    result, client = _run(
        [
            "grid",
            "create",
            "t",
            "--kind",
            "data",
            "--column",
            "b:string",
            "--columns-file",
            str(path),
        ]
    )
    assert result.exit_code != 0
    assert "not both" in _error(result)["error"]["message"]
    client.request.assert_not_called()


def test_column_cap_is_enforced_locally() -> None:
    columns: list[str] = []
    for index in range(51):
        columns += ["--column", f"c{index}:string"]
    result, client = _run(["grid", "create", "t", "--kind", "data", *columns])
    assert result.exit_code != 0
    assert "50-column limit" in _error(result)["error"]["message"]
    client.request.assert_not_called()


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"name": "a", "type": "string"}, "must contain a JSON array"),
        ([{"name": "a"}], "unknown type"),
        ([{"name": "", "type": "string"}], "non-empty string name"),
        ([{"name": "a", "type": "string", "extra": 1}], "unknown keys"),
        ([{"name": "a", "type": "string", "nullable": "yes"}], "non-boolean nullable"),
        (["a:string"], "is not a JSON object"),
    ],
)
def test_columns_file_shape_errors(tmp_path: Path, payload: object, fragment: str) -> None:
    path = tmp_path / "cols.json"
    path.write_text(json.dumps(payload))
    result, client = _run(["grid", "create", "t", "--kind", "data", "--columns-file", str(path)])
    assert result.exit_code != 0
    assert fragment in _error(result)["error"]["message"]
    client.request.assert_not_called()
