"""Tests for shared --wait/--follow behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.stream_options import post_with_wait_follow, validate_wait_follow

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


def test_post_with_wait_follow_forwards_queue_state() -> None:
    """The queue observation must reach the stream, or a queued reply reports nothing."""
    from sum_cli.streaming import QueueObservation

    state = QueueObservation()
    resp = MagicMock()
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = resp
    stream_cm.__exit__.return_value = None
    client = MagicMock()
    client.stream.return_value = stream_cm

    with patch(
        "sum_cli.stream_options.stream_sse_response",
        return_value={"ok": True, "result": {}},
    ) as streamer:
        post_with_wait_follow(
            client, "POST", "/v1/x", wait=True, follow=False, queue_state=state
        )

    assert streamer.call_args.kwargs["queue_state"] is state


def test_stream_open_failure_reports_the_key_instead_of_a_bare_network_error(capsys) -> None:
    """Headers are read on entering the stream — outside stream_sse_response's handler.

    sum-api durably accepts the message before it starts streaming, so a failure in
    that window can leave the turn running while the client sees only an error. The
    generic NETWORK_ERROR from main() would tell the caller to retry, duplicating it.
    """
    import httpx

    from sum_cli.streaming import QueueObservation

    client = MagicMock()
    client.stream.side_effect = httpx.ReadTimeout("timed out waiting for headers")

    with pytest.raises(SystemExit) as exc:
        post_with_wait_follow(
            client,
            "POST",
            "/v1/x",
            wait=True,
            follow=False,
            queue_state=QueueObservation(idempotency_key="send-77"),
        )
    assert exc.value.code == 1
    envelope = json.loads(capsys.readouterr().out.strip())
    assert envelope["ok"] is False
    assert envelope["error"]["data"]["idempotencyKey"] == "send-77"
    assert "send-77" in envelope["fix"]
    assert "retry the stream command" not in envelope["fix"]


def test_stream_open_failure_without_a_key_keeps_the_generic_network_error() -> None:
    """create/reports/grid have nothing to replay; main() still owns those."""
    import httpx

    client = MagicMock()
    client.stream.side_effect = httpx.ConnectError("no route to host")

    with pytest.raises(httpx.HTTPError):
        post_with_wait_follow(client, "POST", "/v1/x", wait=True, follow=False)


def test_stream_open_failure_under_follow_keeps_the_ndjson_shape(capsys) -> None:
    """--follow consumers dispatch on `type`; a bare envelope is invisible to them."""
    import httpx

    from sum_cli.streaming import QueueObservation

    client = MagicMock()
    client.stream.side_effect = httpx.ReadTimeout("timed out")

    with pytest.raises(SystemExit):
        post_with_wait_follow(
            client,
            "POST",
            "/v1/x",
            wait=True,
            follow=True,
            queue_state=QueueObservation(idempotency_key="send-81"),
        )

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().split("\n")]
    assert lines[-1]["type"] == "error"
    assert lines[-1]["error"]["data"]["idempotencyKey"] == "send-81"


def test_a_teardown_error_does_not_discard_an_answer_already_read() -> None:
    """Losing a completed reply to a close-time error is worse than the error."""
    import httpx

    from sum_cli.streaming import QueueObservation

    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = MagicMock()
    stream_cm.__exit__.side_effect = httpx.ReadError("connection reset on close")
    client = MagicMock()
    client.stream.return_value = stream_cm

    with patch(
        "sum_cli.stream_options.stream_sse_response",
        return_value={"ok": True, "result": {"text": "the answer"}},
    ):
        outcome = post_with_wait_follow(
            client,
            "POST",
            "/v1/x",
            wait=True,
            follow=False,
            queue_state=QueueObservation(idempotency_key="send-82"),
        )

    assert outcome.body["text"] == "the answer"
