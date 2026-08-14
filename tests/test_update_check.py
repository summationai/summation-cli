"""Cached, non-blocking PyPI version check and `sumcli update`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from sum_cli import __version__
from sum_cli.cli.main import app
from sum_cli.update_check import (
    PYPI_JSON_URL,
    TTL_SECONDS,
    reset_state,
    resolve_latest,
    run_upgrade,
    warn_if_outdated,
)

runner = CliRunner()


@pytest.fixture
def enable_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUMCLI_NO_UPDATE_CHECK", raising=False)
    reset_state()


def _pypi_ok(version: str) -> httpx.Response:
    request = httpx.Request("GET", PYPI_JSON_URL)
    return httpx.Response(200, request=request, json={"info": {"version": version}})


def test_warns_on_stderr_not_stdout(enable_check, capsys) -> None:
    with patch("sum_cli.update_check.httpx.get", return_value=_pypi_ok("99.0.0")):
        warn_if_outdated(current="0.0.1")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "0.0.1 is behind 99.0.0" in captured.err
    assert "sumcli update" in captured.err


def test_silent_when_current(enable_check, capsys) -> None:
    with patch("sum_cli.update_check.httpx.get", return_value=_pypi_ok("0.0.1")):
        warn_if_outdated(current="0.0.1")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_silent_when_pypi_fails(enable_check, capsys) -> None:
    with patch(
        "sum_cli.update_check.httpx.get",
        side_effect=httpx.ConnectError("offline"),
    ):
        warn_if_outdated(current="0.0.1")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cache_avoids_second_fetch(enable_check) -> None:
    mock_get = MagicMock(return_value=_pypi_ok("99.0.0"))
    with patch("sum_cli.update_check.httpx.get", mock_get):
        assert resolve_latest() == "99.0.0"
        assert resolve_latest() == "99.0.0"
    assert mock_get.call_count == 1
    mock_get.assert_called_with(
        PYPI_JSON_URL,
        timeout=0.4,
        headers={"User-Agent": f"sumcli/{__version__}"},
        follow_redirects=True,
    )


def test_stale_cache_refetches(enable_check) -> None:
    mock_get = MagicMock(return_value=_pypi_ok("2.0.0"))
    with patch("sum_cli.update_check.httpx.get", mock_get):
        assert resolve_latest(now=0) == "2.0.0"
        mock_get.return_value = _pypi_ok("3.0.0")
        assert resolve_latest(now=TTL_SECONDS + 1) == "3.0.0"
    assert mock_get.call_count == 2


def test_env_opt_out_skips_network(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SUMCLI_NO_UPDATE_CHECK", "1")
    reset_state()
    with patch("sum_cli.update_check.httpx.get") as mock_get:
        warn_if_outdated(current="0.0.1")
    mock_get.assert_not_called()
    assert capsys.readouterr().err == ""


def test_version_flag_keeps_json_stdout(enable_check) -> None:
    with patch("sum_cli.update_check.httpx.get", return_value=_pypi_ok("99.0.0")):
        result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["version"] == __version__
    assert "sumcli update" in result.stderr


def test_update_runs_uv_tool_upgrade(enable_check) -> None:
    completed = MagicMock(returncode=0)
    with (
        patch("sum_cli.update_check.shutil.which", return_value="/usr/bin/uv"),
        patch("sum_cli.update_check.subprocess.run", return_value=completed) as run,
    ):
        result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.args[0] == ["/usr/bin/uv", "tool", "upgrade", "summation-cli"]
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["package"] == "summation-cli"


def test_update_errors_when_uv_missing(enable_check) -> None:
    with patch("sum_cli.update_check.shutil.which", return_value=None):
        result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "UV_NOT_FOUND"


def test_update_errors_when_uv_fails(enable_check) -> None:
    completed = MagicMock(returncode=1)
    with (
        patch("sum_cli.update_check.shutil.which", return_value="/usr/bin/uv"),
        patch("sum_cli.update_check.subprocess.run", return_value=completed),
    ):
        result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "UPDATE_FAILED"


def test_run_upgrade_direct_when_uv_missing() -> None:
    """Cover the helper without going through Typer (same envelope as the command)."""
    with (
        patch("sum_cli.update_check.shutil.which", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        run_upgrade()
    assert exc.value.code == 1
