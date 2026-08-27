"""Schedules command tests with mocked HTTP."""

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
    with patch("sum_cli.resources.schedules.api_client", return_value=cm):
        result = runner.invoke(app, args)
    return result, client


def test_list_sends_playbook_kind_and_filters() -> None:
    result, client = _run(
        ["schedules", "list", "--project", "proj_1", "--target", "pb_1"],
        {"data": {"schedules": [{"id": "sch_1"}]}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["schedules"][0]["id"] == "sch_1"
    _, kwargs = client.request.call_args
    assert client.request.call_args[0] == ("GET", "/v1/schedules")
    assert kwargs["params"] == {"kind": "playbook", "project_id": "proj_1", "target_id": "pb_1"}


def test_show_unwraps_data() -> None:
    result, client = _run(["schedules", "show", "sch_1"], {"data": {"id": "sch_1"}})
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["schedule"]["id"] == "sch_1"
    assert client.request.call_args[0] == ("GET", "/v1/schedules/sch_1")


def test_create_builds_target_schedule_and_config() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "weekly",
            "--day",
            "monday",
            "--time-of-day",
            "07:30",
            "--zone",
            "America/Los_Angeles",
            "--param",
            "region=emea",
            "--email",
            "ceo@acme.com:cc:Dana",
            "--output-folder",
            "/Board",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    payload = client.request.call_args[1]["json"]
    assert client.request.call_args[0] == ("POST", "/v1/schedules")
    assert payload["kind"] == "playbook"
    assert payload["target"] == {"project_id": "proj_1", "playbook_id": "pb_1"}
    assert payload["schedule"] == {
        "type": "weekly",
        "time_of_day": "07:30",
        "zone_id": "America/Los_Angeles",
        "days_of_week": ["MONDAY"],
    }
    assert payload["config"]["params"] == {"region": "emea"}
    assert payload["config"]["email_recipients"] == [
        {"email": "ceo@acme.com", "type": "cc", "name": "Dana"}
    ]
    assert payload["config"]["output_folder"] == "/Board"


def test_create_omits_config_when_no_config_flags() -> None:
    result, client = _run(
        ["schedules", "create", "--project", "proj_1", "--playbook", "pb_1", "--type", "daily"],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    payload = client.request.call_args[1]["json"]
    assert "config" not in payload
    assert payload["schedule"] == {"type": "daily"}


def test_create_rejects_unknown_type() -> None:
    result, client = _run(
        ["schedules", "create", "--project", "proj_1", "--playbook", "pb_1", "--type", "hourly"],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_bad_day_and_param() -> None:
    bad_day, day_client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "weekly",
            "--day",
            "funday",
        ],
    )
    assert bad_day.exit_code != 0
    day_client.request.assert_not_called()

    bad_param, param_client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--param",
            "region",
        ],
    )
    assert bad_param.exit_code != 0
    assert json.loads(bad_param.stdout)["error"]["code"] == "INVALID_REQUEST"
    param_client.request.assert_not_called()


def test_create_rejects_email_without_address() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--email",
            ":cc:Dana",
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_unknown_recipient_type() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--email",
            "ops@acme.com:bogus",
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_unreadable_output_config_file(tmp_path: Path) -> None:
    """A directory (not a file) exercises the OSError branch of _load_json_object."""
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--output-config-file",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_sends_description_and_month() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "yearly",
            "--month",
            "3",
            "--day-of-month",
            "15",
            "--description",
            "Quarterly board pack",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    payload = client.request.call_args[1]["json"]
    assert payload["description"] == "Quarterly board pack"
    assert payload["schedule"] == {"type": "yearly", "month": 3, "day_of_month": 15}


def test_create_email_defaults_to_to_type() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--email",
            "ops@acme.com",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    recipients = client.request.call_args[1]["json"]["config"]["email_recipients"]
    assert recipients == [{"email": "ops@acme.com"}]


def test_create_rejects_more_than_50_recipients() -> None:
    args = ["schedules", "create", "--project", "proj_1", "--playbook", "pb_1", "--type", "daily"]
    for i in range(51):
        args += ["--email", f"user{i}@acme.com"]
    result, client = _run(args)
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_accepts_exactly_50_recipients() -> None:
    args = ["schedules", "create", "--project", "proj_1", "--playbook", "pb_1", "--type", "daily"]
    for i in range(50):
        args += ["--email", f"user{i}@acme.com"]
    result, client = _run(args, {"data": {"id": "sch_1"}})
    assert result.exit_code == 0, result.stdout
    assert len(client.request.call_args[1]["json"]["config"]["email_recipients"]) == 50


def test_create_reads_output_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "output.json"
    cfg.write_text(json.dumps({"subject": "Weekly board pack"}))
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--output-config-file",
            str(cfg),
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    output_config = client.request.call_args[1]["json"]["config"]["output_config"]
    assert output_config == {"subject": "Weekly board pack"}


def test_create_rejects_invalid_output_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "output.json"
    cfg.write_text("not json")
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--output-config-file",
            str(cfg),
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_non_utf8_output_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "output.json"
    cfg.write_bytes(b"\xff\xfe\x00\x01")
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--output-config-file",
            str(cfg),
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_rejects_non_object_output_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "output.json"
    cfg.write_text(json.dumps([1, 2]))
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--output-config-file",
            str(cfg),
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_create_paused_sets_config_flag() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--paused",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["config"] == {"paused": True}


def test_update_no_paused_sends_explicit_false() -> None:
    """--no-paused must send paused=false so a PUT can unpause; omitting it sends nothing."""
    result, client = _run(
        [
            "schedules",
            "update",
            "sch_1",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--no-paused",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["config"] == {"paused": False}


def test_update_omitted_paused_sends_no_key() -> None:
    """With no existing config and no flags, `config` stays absent entirely."""
    result, client = _run(
        [
            "schedules",
            "update",
            "sch_1",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert "config" not in client.request.call_args[1]["json"]


def test_update_defaults_playbook_from_existing_target() -> None:
    """sum-api rejects a target change, so the stored playbook is the only valid value."""
    result, client = _run(
        ["schedules", "update", "sch_1", "--project", "proj_1", "--type", "daily"],
        {"data": {"id": "sch_1", "target": {"playbookId": "pb_stored"}}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["target"] == {
        "project_id": "proj_1",
        "playbook_id": "pb_stored",
    }


def test_update_accepts_snake_case_playbook_id() -> None:
    result, client = _run(
        ["schedules", "update", "sch_1", "--project", "proj_1", "--type", "daily"],
        {"data": {"target": {"playbook_id": "pb_snake"}}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["target"]["playbook_id"] == "pb_snake"


def test_update_explicit_playbook_overrides_stored_target() -> None:
    result, client = _run(
        [
            "schedules",
            "update",
            "sch_1",
            "--project",
            "proj_1",
            "--playbook",
            "pb_explicit",
            "--type",
            "daily",
        ],
        {"data": {"target": {"playbookId": "pb_stored"}}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["target"]["playbook_id"] == "pb_explicit"


def test_update_errors_when_no_playbook_available() -> None:
    """No stored target and no flag: report it rather than PUT a null playbook_id."""
    result, client = _run(
        ["schedules", "update", "sch_1", "--project", "proj_1", "--type", "daily"],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    assert all(call[0][0] != "PUT" for call in client.request.call_args_list)


def test_update_warns_about_unmapped_config_keys() -> None:
    """A config key this CLI cannot map is dropped by the full-replace PUT: say so."""
    result, _ = _run(
        [
            "schedules",
            "update",
            "sch_1",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
        ],
        {
            "data": {
                "id": "sch_1",
                "config": {"emailRecipients": [{"email": "a@b.c"}], "retentionDays": 30},
            }
        },
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["unmapped_config_keys"] == ["retentionDays"]
    assert "retentionDays" in body["warning"]


def test_update_does_not_warn_about_known_read_only_keys() -> None:
    """targetAvailable is read-only by design, not an unrecognized field."""
    result, _ = _run(
        [
            "schedules",
            "update",
            "sch_1",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
        ],
        {"data": {"config": {"paused": False, "targetAvailable": True}}},
    )
    assert result.exit_code == 0, result.stdout
    assert "unmapped_config_keys" not in json.loads(result.stdout)["result"]


def test_update_preserves_existing_config() -> None:
    """PUT is a full replace, so unspecified config must be carried over.

    email_recipients/params/output_config have no server default: without the
    merge, changing only the cadence would silently stop all email delivery.
    """
    existing = {
        "data": {
            "id": "sch_1",
            "config": {
                "params": {"region": "emea"},
                "outputFolder": "/Board",
                "outputConfig": {"subject": "Weekly board pack"},
                "emailRecipients": [{"email": "cfo@acme.com", "name": "", "type": "to"}],
                "maxConcurrentRuns": 3,
                "paused": True,
                "targetAvailable": True,
            },
        }
    }
    client, cm = _mock_client()
    client.request.side_effect = [existing, {"data": {"id": "sch_1"}}]
    with patch("sum_cli.resources.schedules.api_client", return_value=cm):
        result = runner.invoke(
            app,
            [
                "schedules",
                "update",
                "sch_1",
                "--project",
                "proj_1",
                "--playbook",
                "pb_1",
                "--type",
                "daily",
                "--time-of-day",
                "10:00",
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args_list[0][0] == ("GET", "/v1/schedules/sch_1")
    config = client.request.call_args_list[1][1]["json"]["config"]
    assert config["email_recipients"] == [{"email": "cfo@acme.com", "name": "", "type": "to"}]
    assert config["params"] == {"region": "emea"}
    assert config["output_folder"] == "/Board"
    assert config["output_config"] == {"subject": "Weekly board pack"}
    assert config["max_concurrent_runs"] == 3
    # paused=False must survive the merge filter too — see test_update_preserves_paused_false.
    assert config["paused"] is True
    # Read-only response fields must not be echoed back into the PUT body.
    assert "targetAvailable" not in config
    assert "target_available" not in config


def test_update_rejects_more_than_50_recipients() -> None:
    """The limit is checked on update too, not just create."""
    args = ["schedules", "update", "sch_1", "--project", "proj_1", "--playbook", "pb_1"]
    args += ["--type", "daily"]
    for i in range(51):
        args += ["--email", f"user{i}@acme.com"]
    result, client = _run(args)
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    client.request.assert_not_called()


def test_update_preserves_paused_false() -> None:
    """paused=False must survive the merge filter.

    _existing_config drops values in (None, {}, []). False is not equal to any of
    them, so it is kept — do not "simplify" that check to a truthiness test, or an
    unpaused schedule would silently revert to the server default on every update.
    """
    existing = {"data": {"id": "sch_1", "config": {"paused": False}}}
    client, cm = _mock_client()
    client.request.side_effect = [existing, {"data": {"id": "sch_1"}}]
    with patch("sum_cli.resources.schedules.api_client", return_value=cm):
        result = runner.invoke(
            app,
            [
                "schedules",
                "update",
                "sch_1",
                "--project",
                "proj_1",
                "--playbook",
                "pb_1",
                "--type",
                "daily",
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args_list[1][1]["json"]["config"] == {"paused": False}


def test_update_flags_override_existing_config() -> None:
    existing = {
        "data": {
            "id": "sch_1",
            "config": {
                "outputFolder": "/Board",
                "emailRecipients": [{"email": "old@acme.com", "name": "", "type": "to"}],
                "paused": True,
            },
        }
    }
    client, cm = _mock_client()
    client.request.side_effect = [existing, {"data": {"id": "sch_1"}}]
    with patch("sum_cli.resources.schedules.api_client", return_value=cm):
        result = runner.invoke(
            app,
            [
                "schedules",
                "update",
                "sch_1",
                "--project",
                "proj_1",
                "--playbook",
                "pb_1",
                "--type",
                "daily",
                "--email",
                "new@acme.com",
                "--no-paused",
            ],
        )
    assert result.exit_code == 0, result.stdout
    config = client.request.call_args_list[1][1]["json"]["config"]
    assert config["email_recipients"] == [{"email": "new@acme.com"}]
    assert config["paused"] is False
    # Untouched field still carried over.
    assert config["output_folder"] == "/Board"


def test_update_uses_put_with_full_payload() -> None:
    result, client = _run(
        [
            "schedules",
            "update",
            "sch_1",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "monthly",
            "--day-of-month",
            "1",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("PUT", "/v1/schedules/sch_1")
    payload = client.request.call_args[1]["json"]
    assert payload["target"] == {"project_id": "proj_1", "playbook_id": "pb_1"}
    assert payload["schedule"] == {"type": "monthly", "day_of_month": 1}


def test_delete_requires_confirm() -> None:
    result, client = _run(["schedules", "delete", "sch_1"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_delete_sends_confirm_param() -> None:
    result, client = _run(["schedules", "delete", "sch_1", "--confirm"])
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("DELETE", "/v1/schedules/sch_1")
    assert client.request.call_args[1]["params"] == {"confirm": True}
    assert json.loads(result.stdout)["result"]["deleted"] == "sch_1"


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_pause_and_resume(action: str) -> None:
    result, client = _run(["schedules", action, "sch_1"], {"data": {"id": "sch_1"}})
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("POST", f"/v1/schedules/sch_1/{action}")
    assert json.loads(result.stdout)["result"]["schedule"]["id"] == "sch_1"


def test_runs_lists_and_truncates() -> None:
    result, client = _run(
        ["schedules", "runs", "sch_1", "--count", "1"],
        {"data": {"runs": [{"id": "run_1"}, {"id": "run_2"}]}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("GET", "/v1/schedules/sch_1/runs")
    body = json.loads(result.stdout)
    assert body["result"]["schedule_id"] == "sch_1"
    assert [r["id"] for r in body["result"]["runs"]] == ["run_1"]


def test_runs_extracts_nested_schedule_runs_executions() -> None:
    result, client = _run(
        ["schedules", "runs", "sch_1"],
        {
            "data": {
                "scheduleRuns": [
                    {
                        "id": "sr_1",
                        "status": "COMPLETED",
                        "executions": [
                            {"id": "exec_1", "state": "SUCCESS"},
                            {"id": "exec_2", "state": "SUCCESS"},
                        ],
                    },
                    {
                        "id": "sr_2",
                        "status": "RUNNING",
                        "executions": [{"id": "exec_3", "state": "RUNNING"}],
                    },
                ]
            }
        },
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("GET", "/v1/schedules/sch_1/runs")
    body = json.loads(result.stdout)
    assert body["result"]["schedule_id"] == "sch_1"
    assert [r["id"] for r in body["result"]["runs"]] == ["exec_1", "exec_2", "exec_3"]
    assert body["result"]["runs"][0]["status"] == "COMPLETED"


def test_run_now_sends_reason() -> None:
    result, client = _run(
        ["schedules", "run", "sch_1", "--confirm", "--reason", "backfill"],
        {"data": {"id": "run_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[0] == ("POST", "/v1/schedules/sch_1/runs")
    assert client.request.call_args[1]["json"] == {"reason": "backfill"}
    assert json.loads(result.stdout)["result"]["run"]["id"] == "run_1"


def test_run_now_sends_effective_run_at() -> None:
    result, client = _run(
        ["schedules", "run", "sch_1", "--confirm", "--effective-run-at", "2026-08-06T09:30:00Z"],
        {"data": {"id": "run_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"] == {"effective_run_at": "2026-08-06T09:30:00Z"}


def test_run_now_requires_confirm() -> None:
    """A manual run delivers real email, so it is gated like delete."""
    result, client = _run(["schedules", "run", "sch_1"], {"data": {"id": "sch_1"}})
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CONFIRM_REQUIRED"
    # The GET is allowed (it names recipients); the run POST must not happen.
    assert all(call[0][0] != "POST" for call in client.request.call_args_list)


def test_run_now_confirm_prompt_names_recipients() -> None:
    """The refusal shows who would be emailed — the point of the gate."""
    result, _ = _run(
        ["schedules", "run", "sch_1"],
        {
            "data": {
                "id": "sch_1",
                "config": {
                    "emailRecipients": [
                        {"email": "cfo@acme.com"},
                        {"email": "board@acme.com"},
                    ]
                },
            }
        },
    )
    assert result.exit_code == 1
    message = json.loads(result.stdout)["error"]["message"]
    assert "cfo@acme.com" in message
    assert "board@acme.com" in message
    assert "2 recipient(s)" in message


def test_run_now_confirm_prompt_truncates_long_recipient_list() -> None:
    result, _ = _run(
        ["schedules", "run", "sch_1"],
        {"data": {"config": {"emailRecipients": [{"email": f"u{i}@acme.com"} for i in range(8)]}}},
    )
    assert result.exit_code == 1
    message = json.loads(result.stdout)["error"]["message"]
    assert "8 recipient(s)" in message
    assert "and 3 more" in message
    assert "u7@acme.com" not in message


def test_run_now_confirm_prompt_does_not_promise_zero_recipients() -> None:
    """An empty/absent recipient list is not proof none exist; do not claim it is."""
    result, _ = _run(["schedules", "run", "sch_1"], {"data": {"id": "sch_1"}})
    message = json.loads(result.stdout)["error"]["message"]
    assert "No recipients were listed" in message
    assert "sends no email" not in message


def test_run_now_with_confirm_skips_the_lookup() -> None:
    """--confirm goes straight to the run; the GET exists only for the prompt."""
    _, client = _run(["schedules", "run", "sch_1", "--confirm"], {"data": {"id": "run_1"}})
    assert [call[0] for call in client.request.call_args_list] == [
        ("POST", "/v1/schedules/sch_1/runs")
    ]


def test_recipient_type_is_lowercased() -> None:
    """Uppercase --email types must normalize; the spec enum is lowercase only."""
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "daily",
            "--email",
            "ops@acme.com:CC",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    recipients = client.request.call_args[1]["json"]["config"]["email_recipients"]
    assert recipients == [{"email": "ops@acme.com", "type": "cc"}]


def test_create_cron_cadence() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "cron",
            "--cron",
            "0 9 * * 1",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["schedule"] == {
        "type": "cron",
        "cron_expression": "0 9 * * 1",
    }


def test_create_interval_cadence() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "interval",
            "--every-minutes",
            "30",
            "--interval",
            "2",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"]["schedule"] == {
        "type": "interval",
        "every_minutes": 30,
        "interval": 2,
    }


def test_create_one_time_cadence_with_anchor() -> None:
    result, client = _run(
        [
            "schedules",
            "create",
            "--project",
            "proj_1",
            "--playbook",
            "pb_1",
            "--type",
            "one_time",
            "--run-date",
            "2026-09-01",
            "--anchor-date",
            "2026-08-01",
            "--max-concurrent-runs",
            "3",
        ],
        {"data": {"id": "sch_1"}},
    )
    assert result.exit_code == 0, result.stdout
    payload = client.request.call_args[1]["json"]
    assert payload["schedule"] == {
        "type": "one_time",
        "run_date": "2026-09-01",
        "anchor_date": "2026-08-01",
    }
    assert payload["config"]["max_concurrent_runs"] == 3


def test_run_now_defaults_to_empty_body() -> None:
    result, client = _run(["schedules", "run", "sch_1", "--confirm"], {"data": {"id": "run_1"}})
    assert result.exit_code == 0, result.stdout
    assert client.request.call_args[1]["json"] == {}
