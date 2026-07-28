"""Root CLI behavior tests."""

from __future__ import annotations

import io
import json
import sys
import time
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli import __version__
from sum_cli.cli.main import app, main
from sum_cli.client import ApiError
from sum_cli.output import get_output_mode, set_output_mode

runner = CliRunner()


def test_output_flag_before_subcommand_sets_human(monkeypatch):
    # --output is a root option: it must precede the subcommand. The eager callback
    # sets the mode before the command runs.
    monkeypatch.delenv("SUMCLI_OUTPUT", raising=False)
    set_output_mode("json")
    runner.invoke(app, ["--output", "human", "projects", "list"])
    assert get_output_mode() == "human"


def test_output_flag_after_subcommand_is_an_error(monkeypatch):
    # Root-option only: placing --output after the subcommand is a clean error, not
    # silent corruption of a sibling option's value (the argv-prescan failure mode).
    monkeypatch.delenv("SUMCLI_OUTPUT", raising=False)
    result = runner.invoke(app, ["projects", "list", "--output", "human"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_output_env_var_sets_mode(monkeypatch):
    monkeypatch.setenv("SUMCLI_OUTPUT", "human")
    set_output_mode("json")
    runner.invoke(app, ["projects", "list"])
    assert get_output_mode() == "human"


def test_output_flag_rejects_invalid_value(monkeypatch):
    # --output is a Choice: a typo is rejected at parse time rather than silently
    # falling through to the env/TTY default.
    monkeypatch.delenv("SUMCLI_OUTPUT", raising=False)
    result = runner.invoke(app, ["--output", "josn", "projects", "list"])
    assert result.exit_code != 0
    assert "josn" in result.output


def test_output_env_var_invalid_falls_back(monkeypatch):
    # A typo in SUMCLI_OUTPUT stays lenient (only the explicit flag is strictly
    # validated): it is ignored and resolution falls through to the TTY default,
    # which is json under the non-interactive test runner.
    monkeypatch.setenv("SUMCLI_OUTPUT", "josn")
    set_output_mode("human")
    result = runner.invoke(app, ["projects", "list"])
    assert result.exit_code == 0 or "josn" not in result.output
    assert get_output_mode() == "json"


def test_command_field_no_duplicate_sumcli(tmp_path, monkeypatch) -> None:
    from sum_cli.config_store import write_all

    cfg_file = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUM_API_ACCESS_TOKEN", raising=False)
    # CliRunner does not populate sys.argv, which is where the command path is
    # resolved from; set it to the invocation under test.
    monkeypatch.setattr(sys, "argv", ["sumcli", "auth", "token"])
    write_all(
        cfg_file,
        {
            "_meta": {"active_profile": "p"},
            "p": {
                "base_url": "https://example.com",
                "client_id": "cid",
                "client_secret": "csecret",
                "access_token": "tok",
                "token_expires_at": str(int(time.time()) + 3600),
            },
        },
    )
    result = runner.invoke(app, ["auth", "token"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["command"] == "sumcli auth token"


def test_root_command_tree() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert "projects" in body["result"]["resources"]


def test_root_command_tree_openapi_spec_missing() -> None:
    from sum_cli.openapi_doc import OpenApiSpecError

    with patch(
        "sum_cli.cli.main.build_command_tree_envelope",
        side_effect=OpenApiSpecError("snapshot missing"),
    ):
        result = runner.invoke(app, [])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "OPENAPI_SPEC_MISSING"


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["version"] == __version__


def test_api_error_envelope(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = ApiError(
        404,
        {"error": {"code": "NOT_FOUND", "message": "missing"}},
    )
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setattr(sys, "argv", ["sumcli", "projects", "list"])

    buf = io.StringIO()
    with patch("sum_cli.resources.projects.api_client", return_value=mock_cm):
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                main()
    assert exc.value.code == 1
    body = json.loads(buf.getvalue())
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "missing"
    assert len(body["next_actions"]) >= 1


def test_auth_error_envelope(monkeypatch, tmp_path) -> None:
    cfg_file = tmp_path / "config"
    cfg_file.write_text("")
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUM_API_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SUM_API_CLIENT_ID", raising=False)
    monkeypatch.delenv("SUM_API_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setattr(sys, "argv", ["sumcli", "auth", "token"])

    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1
    body = json.loads(buf.getvalue())
    assert body["ok"] is False
    assert body["error"]["code"] == "AUTH_ERROR"
    assert len(body["next_actions"]) >= 1
