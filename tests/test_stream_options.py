"""Tests for shared --wait/--follow behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.stream_options import validate_wait_follow

runner = CliRunner()


def test_validate_wait_follow_rejects_follow_without_wait() -> None:
    with pytest.raises(SystemExit):
        validate_wait_follow(wait=False, follow=True)


def test_reports_generate_default_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with (
        patch("sum_cli.resources.reports.api_client", return_value=mock_cm),
        patch(
            "sum_cli.stream_options.stream_sse_response",
            return_value={"ok": True, "result": {"report": {"id": "rpt_1"}}},
        ),
    ):
        result = runner.invoke(app, ["reports", "generate", "-m", "hello"])

    assert result.exit_code == 0
    mock_client.stream.assert_called_once()
    mock_client.request.assert_not_called()


def test_reports_generate_no_follow_still_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-follow`` keeps --wait semantics but prints one envelope, not NDJSON."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with (
        patch("sum_cli.resources.reports.api_client", return_value=mock_cm),
        patch(
            "sum_cli.stream_options.stream_sse_response",
            return_value={"ok": True, "result": {"report": {"id": "rpt_1"}}},
        ),
    ):
        result = runner.invoke(
            app,
            ["reports", "generate", "-m", "hello", "--no-follow"],
        )

    assert result.exit_code == 0
    assert "INVALID_FLAGS" not in result.output
    mock_client.stream.assert_called_once()


def test_reports_generate_no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with (
        patch("sum_cli.resources.reports.api_client", return_value=mock_cm),
        patch(
            "sum_cli.stream_options.stream_sse_response",
            return_value={"ok": True, "result": {"report": {"id": "rpt_1"}}},
        ),
    ):
        result = runner.invoke(
            app,
            ["reports", "generate", "-m", "hello", "--no-wait"],
        )

    assert result.exit_code == 0
    mock_client.stream.assert_called_once()
    mock_client.request.assert_not_called()


def test_reports_generate_no_wait_rejects_explicit_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with (
        patch("sum_cli.resources.reports.api_client", return_value=mock_cm),
        patch(
            "sum_cli.stream_options.stream_sse_response",
            return_value={"ok": True, "result": {"report": {"id": "rpt_1"}}},
        ),
    ):
        result = runner.invoke(
            app,
            ["reports", "generate", "-m", "hello", "--no-wait", "--follow"],
        )

    # Explicit --follow with --no-wait is rejected the same way as on chats/grid;
    # no request is attempted.
    assert result.exit_code == 1
    assert "INVALID_FLAGS" in result.output
    mock_client.stream.assert_not_called()
    mock_client.request.assert_not_called()


def test_reports_generate_stream_error_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    error_terminal = {
        "ok": False,
        "error": {"code": "STREAM_ERROR", "message": "failed"},
        "fix": "retry",
    }

    with (
        patch("sum_cli.resources.reports.api_client", return_value=mock_cm),
        patch(
            "sum_cli.stream_options.stream_sse_response",
            return_value=error_terminal,
        ),
    ):
        result = runner.invoke(app, ["reports", "generate", "-m", "hello"])

    assert result.exit_code == 1


def _stream_client_mock() -> tuple[MagicMock, MagicMock]:
    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None
    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_client, mock_cm


def test_chats_create_stream_error_is_top_level_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a mid-stream error must set top-level ok:false + exit 1,
    not be nested under result.chat with ok:true (DevX feedback #2)."""
    import json

    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    _, mock_cm = _stream_client_mock()
    error_terminal = {
        "ok": False,
        "command": "sumcli",
        "error": {
            "code": "upstream_api_error",
            "message": "API Error: 500",
            "data": {"code": "upstream_api_error", "message": "API Error: 500"},
        },
        "fix": "Inspect error.data and retry.",
        "next_actions": [],
    }

    with (
        patch("sum_cli.resources.chats.api_client", return_value=mock_cm),
        patch("sum_cli.stream_options.stream_sse_response", return_value=error_terminal),
    ):
        result = runner.invoke(app, ["chats", "create", "-m", "hello"])

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    # Top-level ok must be False — not a success envelope wrapping a failure.
    assert body["ok"] is False
    assert "chat" not in body.get("result", {})
    assert body["error"]["code"] == "upstream_api_error"
    # Structured data is preserved as JSON, not a stringified Python dict (#3).
    assert body["error"]["data"]["code"] == "upstream_api_error"
