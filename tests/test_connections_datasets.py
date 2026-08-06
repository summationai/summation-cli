"""Connection dataset and snapshot command tests with mocked HTTP."""

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
    with patch("sum_cli.resources.connections.api_client", return_value=cm):
        result = runner.invoke(app, args)
    return result, client


# --- create (--config-file handling) ----------------------------------------


def test_create_sends_config_and_secrets_from_file(tmp_path: Path) -> None:
    config_file = tmp_path / "sf.json"
    config_file.write_text(
        json.dumps(
            {
                "config": {"snowflake_account": "myorg-acct1", "snowflake_username": "svc"},
                "secrets": {"snowflake_password": "pw"},
            }
        )
    )
    _, client = _run(
        [
            "connections",
            "create",
            "--name",
            "prod-sf",
            "--type",
            "SNOWFLAKE",
            "--config-file",
            str(config_file),
        ],
        {"data": {"id": "conn_1"}},
    )
    sent = client.request.call_args[1]["json"]
    assert sent["name"] == "prod-sf"
    assert sent["type"] == "SNOWFLAKE"
    assert sent["config"]["snowflake_account"] == "myorg-acct1"
    assert sent["secrets"]["snowflake_password"] == "pw"


def test_create_file_config_overwrites_rather_than_defaults(tmp_path: Path) -> None:
    """Regression: the file is the caller's explicit input, so it must land on the
    payload verbatim. ``setdefault`` would make this a silent no-op if anything
    ever populated config/secrets first."""
    config_file = tmp_path / "sf.json"
    config_file.write_text(
        json.dumps({"config": {"host": "from-file"}, "secrets": {"password": "from-file"}})
    )
    _, client = _run(
        [
            "connections",
            "create",
            "--name",
            "n",
            "--type",
            "POSTGRES",
            "--config-file",
            str(config_file),
        ],
        {"data": {}},
    )
    sent = client.request.call_args[1]["json"]
    assert sent["config"] == {"host": "from-file"}
    assert sent["secrets"] == {"password": "from-file"}


def test_create_omits_config_keys_without_file() -> None:
    """No --config-file means no empty config/secrets on the request."""
    _, client = _run(
        ["connections", "create", "--name", "n", "--type", "POSTGRES"],
        {"data": {}},
    )
    sent = client.request.call_args[1]["json"]
    assert "config" not in sent
    assert "secrets" not in sent


def test_create_rejects_non_object_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "sf.json"
    config_file.write_text(json.dumps(["not", "an", "object"]))
    result, client = _run(
        [
            "connections",
            "create",
            "--name",
            "n",
            "--type",
            "SNOWFLAKE",
            "--config-file",
            str(config_file),
        ]
    )
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_REQUEST"
    # Hint must describe this flag's shape, not the datasets shape.
    assert "secrets" in body["fix"]
    client.request.assert_not_called()


def test_create_rejects_invalid_json_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "sf.json"
    config_file.write_text("{not json")
    result, client = _run(
        [
            "connections",
            "create",
            "--name",
            "n",
            "--type",
            "SNOWFLAKE",
            "--config-file",
            str(config_file),
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_non_utf8_config_file(tmp_path: Path) -> None:
    """Previously raised UnicodeDecodeError instead of a clean CLI error."""
    config_file = tmp_path / "sf.json"
    config_file.write_bytes(b"\xff\xfe{invalid}")
    result, client = _run(
        [
            "connections",
            "create",
            "--name",
            "n",
            "--type",
            "SNOWFLAKE",
            "--config-file",
            str(config_file),
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_unreadable_config_file(tmp_path: Path) -> None:
    """Previously raised a bare OSError traceback."""
    missing = tmp_path / "does-not-exist.json"
    result, client = _run(
        [
            "connections",
            "create",
            "--name",
            "n",
            "--type",
            "SNOWFLAKE",
            "--config-file",
            str(missing),
        ]
    )
    assert result.exit_code != 0
    client.request.assert_not_called()


def test_attach_datasets_hint_names_datasets_shape(tmp_path: Path) -> None:
    """The shared loader must report the caller's shape, not a fixed one."""
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(json.dumps(["not", "an", "object"]))
    result, _ = _run(
        ["connections", "attach-datasets", "conn_1", "--datasets-file", str(spec_file)]
    )
    assert result.exit_code != 0
    assert "from_source" in json.loads(result.stdout)["fix"]


# --- datasets ---------------------------------------------------------------


def test_datasets_lists_and_echoes_connection() -> None:
    result, client = _run(
        ["connections", "datasets", "conn_1"],
        {"data": {"datasets": [{"id": "ds_1", "status": "DEPLOYED"}], "total": 1}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["datasets"][0]["id"] == "ds_1"
    assert body["result"]["connection_id"] == "conn_1"
    assert client.request.call_args[0] == ("GET", "/v1/connections/data/conn_1/datasets")


def test_datasets_truncates_with_count() -> None:
    result, _ = _run(
        ["connections", "datasets", "conn_1", "--count", "1"],
        {"data": {"datasets": [{"id": "ds_1"}, {"id": "ds_2"}]}},
    )
    assert result.exit_code == 0, result.stdout
    listed = json.loads(result.stdout)["result"]["datasets"]
    assert [d["id"] for d in listed] == ["ds_1"]


def test_datasets_handles_empty_payload() -> None:
    result, _ = _run(["connections", "datasets", "conn_1"], {"data": {}})
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["datasets"] == []


# --- attach-datasets --------------------------------------------------------


def test_attach_builds_specs_from_repeated_source() -> None:
    result, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--from-source",
            "db.public.orders",
            "--from-source",
            "db.public.users",
        ],
        {"data": {"datasets": [{"id": "ds_1"}, {"id": "ds_2"}]}},
    )
    assert result.exit_code == 0, result.stdout
    args, kwargs = client.request.call_args
    assert args == ("POST", "/v1/connections/data/conn_1/datasets")
    assert kwargs["json"] == {
        "datasets": [
            {"from_source": "db.public.orders"},
            {"from_source": "db.public.users"},
        ]
    }
    body = json.loads(result.stdout)
    assert [d["id"] for d in body["result"]["datasets"]] == ["ds_1", "ds_2"]


def test_attach_applies_name_and_description_to_single_source() -> None:
    _, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--from-source",
            "db.public.orders",
            "--name",
            "orders",
            "--description",
            "Order facts",
        ],
        {"data": {"datasets": []}},
    )
    assert client.request.call_args[1]["json"]["datasets"] == [
        {
            "from_source": "db.public.orders",
            "name": "orders",
            "description": "Order facts",
        }
    ]


def test_attach_rejects_name_with_multiple_sources() -> None:
    result, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--from-source",
            "a",
            "--from-source",
            "b",
            "--name",
            "collides",
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_attach_snapshot_flag_applies_to_every_spec() -> None:
    _, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--from-source",
            "a",
            "--from-source",
            "b",
            "--snapshot-enabled",
        ],
        {"data": {"datasets": []}},
    )
    specs = client.request.call_args[1]["json"]["datasets"]
    assert all(spec["snapshot_enabled"] is True for spec in specs)


def test_attach_no_snapshot_flag_sends_false() -> None:
    _, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--from-source",
            "a",
            "--no-snapshot-enabled",
        ],
        {"data": {"datasets": []}},
    )
    assert client.request.call_args[1]["json"]["datasets"][0]["snapshot_enabled"] is False


def test_attach_omits_snapshot_when_flag_absent() -> None:
    """Omitting the flag must inherit the connection policy, not send a default."""
    _, client = _run(
        ["connections", "attach-datasets", "conn_1", "--from-source", "a"],
        {"data": {"datasets": []}},
    )
    assert "snapshot_enabled" not in client.request.call_args[1]["json"]["datasets"][0]


def test_attach_reads_datasets_file_with_params(tmp_path: Path) -> None:
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "params": {
                            "http_request_path": "/v2/charges",
                            "pagination_data_pointer": "/data",
                        }
                    }
                ]
            }
        )
    )
    _, client = _run(
        ["connections", "attach-datasets", "conn_1", "--datasets-file", str(spec_file)],
        {"data": {"datasets": []}},
    )
    sent = client.request.call_args[1]["json"]["datasets"]
    assert sent[0]["params"]["http_request_path"] == "/v2/charges"
    assert "from_source" not in sent[0]


def test_attach_flag_overrides_snapshot_in_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(
        json.dumps({"datasets": [{"from_source": "a", "snapshot_enabled": False}]})
    )
    _, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--datasets-file",
            str(spec_file),
            "--snapshot-enabled",
        ],
        {"data": {"datasets": []}},
    )
    assert client.request.call_args[1]["json"]["datasets"][0]["snapshot_enabled"] is True


def test_attach_rejects_file_and_source_together(tmp_path: Path) -> None:
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(json.dumps({"datasets": [{"from_source": "a"}]}))
    result, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--from-source",
            "b",
            "--datasets-file",
            str(spec_file),
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_attach_requires_some_input() -> None:
    result, client = _run(["connections", "attach-datasets", "conn_1"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_attach_rejects_empty_datasets_array(tmp_path: Path) -> None:
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(json.dumps({"datasets": []}))
    result, client = _run(
        ["connections", "attach-datasets", "conn_1", "--datasets-file", str(spec_file)]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_attach_rejects_invalid_json_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text("{not json")
    result, client = _run(
        ["connections", "attach-datasets", "conn_1", "--datasets-file", str(spec_file)]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_attach_enforces_batch_limit() -> None:
    sources: list[str] = []
    for index in range(101):
        sources += ["--from-source", f"tbl_{index}"]
    result, client = _run(["connections", "attach-datasets", "conn_1", *sources])
    assert result.exit_code != 0
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "100" in error["message"]
    client.request.assert_not_called()


# --- snapshot ---------------------------------------------------------------


def test_snapshot_posts_to_dataset_and_echoes_ids() -> None:
    result, client = _run(
        ["connections", "snapshot", "conn_1", "ds_1"],
        {"data": {"status": "queued", "submitted": 1}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["snapshot"]["status"] == "queued"
    assert body["connection_id"] == "conn_1"
    assert body["dataset_id"] == "ds_1"
    assert client.request.call_args[0] == (
        "POST",
        "/v1/connections/data/conn_1/datasets/ds_1/snapshots",
    )


def test_snapshot_sends_no_body() -> None:
    """The route takes no request body; sending one risks a 422 on a stricter server."""
    _, client = _run(["connections", "snapshot", "conn_1", "ds_1"], {"data": {}})
    assert "json" not in client.request.call_args[1]


# --- snapshots --------------------------------------------------------------


def test_snapshots_defaults_limit_to_ten() -> None:
    result, client = _run(
        ["connections", "snapshots", "conn_1"],
        {"data": {"runs": [{"id": "run_1", "status": "COMPLETED"}]}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["runs"][0]["id"] == "run_1"
    assert body["connection_id"] == "conn_1"
    args, kwargs = client.request.call_args
    assert args == ("GET", "/v1/connections/data/conn_1/snapshots")
    assert kwargs["params"] == {"limit": 10}


def test_snapshots_passes_explicit_limit() -> None:
    _, client = _run(
        ["connections", "snapshots", "conn_1", "--limit", "50"],
        {"data": {"runs": []}},
    )
    assert client.request.call_args[1]["params"] == {"limit": 50}


@pytest.mark.parametrize("limit", ["0", "51"])
def test_snapshots_rejects_out_of_range_limit(limit: str) -> None:
    result, client = _run(["connections", "snapshots", "conn_1", "--limit", limit])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()
