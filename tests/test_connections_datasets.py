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


def test_create_forwards_snapshot_config_from_file(tmp_path: Path) -> None:
    """Regression: snapshot_config is a ConnectionWriteRequest field, and was dropped
    silently, so a user following the snapshot workflow got exit 0 and no policy."""
    config_file = tmp_path / "http.json"
    config_file.write_text(
        json.dumps(
            {
                "config": {"base_url": "https://api.example.com"},
                "secrets": {"token": "t"},
                "snapshot_config": {"enabled": True, "version": 1},
            }
        )
    )
    _, client = _run(
        [
            "connections",
            "create",
            "--name",
            "n",
            "--type",
            "HTTP",
            "--config-file",
            str(config_file),
        ],
        {"data": {}},
    )
    sent = client.request.call_args[1]["json"]
    assert sent["snapshot_config"] == {"enabled": True, "version": 1}


def test_create_omits_snapshot_config_when_file_lacks_it(tmp_path: Path) -> None:
    """An absent snapshot_config means "leave the policy unchanged"; sending an empty
    object would be a real instruction, so the key must not appear at all."""
    config_file = tmp_path / "pg.json"
    config_file.write_text(json.dumps({"config": {"host": "h"}, "secrets": {}}))
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
    assert "snapshot_config" not in client.request.call_args[1]["json"]


@pytest.mark.parametrize("key", ["snapshotConfig", "name", "type", "description", "typo"])
def test_create_rejects_unknown_config_file_keys(tmp_path: Path, key: str) -> None:
    """Unknown keys were dropped without a word. A camelCase or misspelled
    snapshot_config is exactly the case that must not exit 0."""
    config_file = tmp_path / "sf.json"
    config_file.write_text(json.dumps({"config": {"host": "h"}, key: "whatever"}))
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
    assert key in body["error"]["message"]
    client.request.assert_not_called()


def test_create_reports_every_unknown_config_file_key(tmp_path: Path) -> None:
    """All offenders at once, so the caller fixes the file in one pass."""
    config_file = tmp_path / "sf.json"
    config_file.write_text(json.dumps({"alpha": 1, "zeta": 2, "config": {}}))
    result, _ = _run(
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
    message = json.loads(result.stdout)["error"]["message"]
    assert "alpha" in message
    assert "zeta" in message


@pytest.mark.parametrize(
    "body",
    [
        {"config": "a string"},
        {"secrets": None},
        {"config": []},
        {"config": {"host": "h"}, "secrets": 123},
    ],
)
@pytest.mark.parametrize("command", ["create", "update"])
def test_config_file_rejects_non_object_values(tmp_path: Path, command: str, body: dict) -> None:
    """Key names were checked but value types were not, so a scalar reached the wire
    and came back as a server 422 the caller had to decode. Both commands share the
    validator, so both reject it client-side."""
    config_file = tmp_path / "c.json"
    config_file.write_text(json.dumps(body))
    args = (
        ["connections", "create", "--name", "n", "--type", "T", "--config-file", str(config_file)]
        if command == "create"
        else ["connections", "update", "c1", "--config-file", str(config_file)]
    )
    result, client = _run(args)
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "INVALID_REQUEST"
    assert "must be JSON objects" in payload["error"]["message"]
    client.request.assert_not_called()


def test_config_file_reports_every_non_object_value(tmp_path: Path) -> None:
    config_file = tmp_path / "c.json"
    config_file.write_text(json.dumps({"config": "x", "secrets": 1}))
    result, _ = _run(
        ["connections", "create", "--name", "n", "--type", "T", "--config-file", str(config_file)]
    )
    message = json.loads(result.stdout)["error"]["message"]
    assert "config" in message
    assert "secrets" in message


# --- update (--config-file handling) ----------------------------------------


def test_update_rotates_secrets_without_clearing_config(tmp_path: Path) -> None:
    """PATCH leaves omitted top-level fields unchanged, so a rotation must send
    secrets alone. A present key replaces that stored object wholesale — this test
    only proves top-level isolation; see help text for the sub-key replace rule."""
    config_file = tmp_path / "rotate.json"
    config_file.write_text(json.dumps({"secrets": {"snowflake_password": "new-pw"}}))
    _, client = _run(
        ["connections", "update", "conn_1", "--config-file", str(config_file)],
        {"data": {"id": "conn_1"}},
    )
    sent = client.request.call_args[1]["json"]
    assert sent == {"secrets": {"snowflake_password": "new-pw"}}
    assert "config" not in sent
    assert "snapshot_config" not in sent


def test_update_sends_snapshot_config_alone(tmp_path: Path) -> None:
    """Editing only the snapshot policy must not touch config or secrets."""
    config_file = tmp_path / "snap.json"
    config_file.write_text(json.dumps({"snapshot_config": {"enabled": True, "version": 1}}))
    _, client = _run(
        ["connections", "update", "conn_1", "--config-file", str(config_file)],
        {"data": {}},
    )
    assert client.request.call_args[1]["json"] == {
        "snapshot_config": {"enabled": True, "version": 1}
    }


def test_update_never_defaults_snapshot_config(tmp_path: Path) -> None:
    """The load-bearing case. A present snapshot_config replaces the stored policy,
    and the server accepts an empty object, so a `.get("snapshot_config", {})` default
    would erase the policy on a rotation that never mentioned snapshotting."""
    config_file = tmp_path / "rotate.json"
    config_file.write_text(json.dumps({"secrets": {"password": "new"}}))
    _, client = _run(
        ["connections", "update", "conn_1", "--config-file", str(config_file)],
        {"data": {}},
    )
    assert "snapshot_config" not in client.request.call_args[1]["json"]


def test_update_combines_flags_and_file(tmp_path: Path) -> None:
    config_file = tmp_path / "c.json"
    config_file.write_text(json.dumps({"config": {"host": "h2"}}))
    _, client = _run(
        [
            "connections",
            "update",
            "conn_1",
            "--name",
            "renamed",
            "--config-file",
            str(config_file),
        ],
        {"data": {}},
    )
    sent = client.request.call_args[1]["json"]
    assert sent == {"name": "renamed", "config": {"host": "h2"}}


def test_update_rejects_unknown_config_file_keys(tmp_path: Path) -> None:
    config_file = tmp_path / "c.json"
    config_file.write_text(json.dumps({"snapshotConfig": {"enabled": True}}))
    result, client = _run(["connections", "update", "conn_1", "--config-file", str(config_file)])
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert "snapshotConfig" in body["error"]["message"]
    client.request.assert_not_called()


def test_update_rejects_empty_change_set() -> None:
    """PATCH {} succeeds and changes nothing; exit 0 would read as "updated"."""
    result, client = _run(["connections", "update", "conn_1"])
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert "--config-file" in body["fix"]
    client.request.assert_not_called()


def test_update_rejects_empty_config_file(tmp_path: Path) -> None:
    """`--config-file {}` must not hint "pass --config-file" — the caller already did."""
    config_file = tmp_path / "empty.json"
    config_file.write_text("{}")
    result, client = _run(["connections", "update", "conn_1", "--config-file", str(config_file)])
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert "has none of" in body["error"]["message"]
    assert "config, secrets, snapshot_config" in body["error"]["message"]
    # Circular hint: "Pass --config-file" when they already passed it.
    assert "Pass --name" not in body["fix"]
    client.request.assert_not_called()


def test_config_file_shape_hint_includes_snapshot_config(tmp_path: Path) -> None:
    """snapshot_config is rejected-not-dropped; the discoverable hint must name it."""
    config_file = tmp_path / "bad.json"
    config_file.write_text(json.dumps(["not", "an", "object"]))
    result, _ = _run(
        ["connections", "create", "--name", "n", "--type", "T", "--config-file", str(config_file)]
    )
    assert result.exit_code != 0
    fix = json.loads(result.stdout)["fix"]
    assert "snapshot_config" in fix


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


@pytest.mark.parametrize(
    "entries",
    [
        ["db.public.orders", "db.public.users"],
        [None],
        [{"from_source": "a"}, "b"],
        [["nested"]],
    ],
    ids=["strings", "null", "mixed", "nested-list"],
)
def test_attach_rejects_non_object_dataset_entries(tmp_path: Path, entries: list) -> None:
    """Regression: assigning --snapshot-enabled into a non-dict entry raised TypeError,
    surfacing as INTERNAL_ERROR with a useless 'retry' hint."""
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(json.dumps({"datasets": entries}))
    result, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--datasets-file",
            str(spec_file),
            "--snapshot-enabled",
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_attach_rejects_non_object_entries_without_snapshot_flag(tmp_path: Path) -> None:
    """The flag must not change whether malformed input is caught — before the fix,
    omitting it forwarded the bad body to the server instead."""
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(json.dumps({"datasets": ["db.public.orders"]}))
    result, client = _run(
        ["connections", "attach-datasets", "conn_1", "--datasets-file", str(spec_file)]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


@pytest.mark.parametrize("flag,value", [("--name", "n"), ("--description", "d")])
def test_attach_rejects_name_or_description_with_datasets_file(
    tmp_path: Path, flag: str, value: str
) -> None:
    """Regression: these were silently dropped, exiting 0 while the flags vanished."""
    spec_file = tmp_path / "datasets.json"
    spec_file.write_text(json.dumps({"datasets": [{"from_source": "db.public.orders"}]}))
    result, client = _run(
        [
            "connections",
            "attach-datasets",
            "conn_1",
            "--datasets-file",
            str(spec_file),
            flag,
            value,
        ]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


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


# --- detach-dataset ---------------------------------------------------------


def test_detach_dataset_requires_confirm() -> None:
    result, client = _run(["connections", "detach-dataset", "conn_1", "ds_1"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_detach_dataset_no_wait_deletes_without_polling() -> None:
    result, client = _run(
        ["connections", "detach-dataset", "conn_1", "ds_1", "--confirm", "--no-wait"],
        {"data": {}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["detached"] == "ds_1"
    assert body["connection_id"] == "conn_1"
    assert body["teardown"] == "pending"
    client.request.assert_called_once_with(
        "DELETE",
        "/v1/connections/data/conn_1/datasets/ds_1",
        params={"confirm": True},
    )


def test_detach_dataset_wait_polls_until_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sum_cli.resources.connections.time.sleep", lambda _s: None)
    client, cm = _mock_client()
    client.request.side_effect = [
        {"data": {}},
        {"data": {"datasets": [{"id": "ds_1"}, {"id": "ds_2"}]}},
        {"data": {"datasets": [{"id": "ds_2"}]}},
    ]
    with patch("sum_cli.resources.connections.api_client", return_value=cm):
        result = runner.invoke(
            app, ["connections", "detach-dataset", "conn_1", "ds_1", "--confirm"]
        )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["teardown"] == "complete"
    assert body["detached"] == "ds_1"
    calls = [c.args[:2] for c in client.request.call_args_list]
    assert calls == [
        ("DELETE", "/v1/connections/data/conn_1/datasets/ds_1"),
        ("GET", "/v1/connections/data/conn_1/datasets"),
        ("GET", "/v1/connections/data/conn_1/datasets"),
    ]


def test_detach_dataset_wait_matches_camel_case_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sum_cli.resources.connections.time.sleep", lambda _s: None)
    client, cm = _mock_client()
    client.request.side_effect = [
        {"data": {}},
        {"data": {"datasets": [{"datasetId": "ds_1"}]}},
        {"data": {"datasets": []}},
    ]
    with patch("sum_cli.resources.connections.api_client", return_value=cm):
        result = runner.invoke(
            app, ["connections", "detach-dataset", "conn_1", "ds_1", "--confirm"]
        )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["teardown"] == "complete"
    assert client.request.call_count == 3


def test_detach_dataset_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sum_cli.resources.connections.time.sleep", lambda _s: None)
    monkeypatch.setattr("sum_cli.resources.connections._DETACH_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr("sum_cli.resources.connections._DETACH_POLL_SECONDS", 0)
    client, cm = _mock_client()
    client.request.side_effect = [
        {"data": {}},
        {"data": {"datasets": [{"id": "ds_1"}]}},
    ]
    with patch("sum_cli.resources.connections.api_client", return_value=cm):
        result = runner.invoke(
            app, ["connections", "detach-dataset", "conn_1", "ds_1", "--confirm"]
        )
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "DETACH_TIMEOUT"
    assert "ds_1" in error["message"]
