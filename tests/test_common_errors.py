"""CLI error envelope integration tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()


def test_files_list_no_project(monkeypatch, tmp_path) -> None:
    cfg_file = tmp_path / "config"
    cfg_file.write_text("")
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("SUMMATION_PROJECT", raising=False)
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    result = runner.invoke(app, ["files", "list"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "NO_PROJECT"
    assert "set-project" in body["fix"] or "--project" in body["fix"]
    assert len(body["next_actions"]) >= 1


def test_files_delete_forwards_recursive(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.request.return_value = None
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    with patch("sum_cli.resources.files.api_client", return_value=mock_cm):
        result = runner.invoke(
            app,
            ["files", "delete", "file_1", "--project", "proj_1", "--confirm"],
        )

    assert result.exit_code == 0
    mock_client.request.assert_called_once_with(
        "DELETE",
        "/v1/projects/proj_1/files/file_1",
        params={"recursive": True, "confirm": True},
    )


def test_connections_delete_requires_confirm(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    result = runner.invoke(app, ["connections", "delete", "conn_1"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CONFIRM_REQUIRED"


def test_queries_run_requires_sql_or_file(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")

    result = runner.invoke(app, ["queries", "run"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_REQUEST"


def _problem_envelope(body: dict, status: int = 409) -> dict:
    """Render an ApiError the way main() does, without a live HTTP call."""
    from sum_cli.cli.main import _api_error_envelope
    from sum_cli.client import ApiError

    return _api_error_envelope(ApiError(status, body))


def test_conversation_busy_keeps_the_active_message_id_and_queue_guidance() -> None:
    """The recovery ID must survive as structured data, not be flattened into prose."""
    envelope = _problem_envelope(
        {
            "code": "conversation_busy",
            "detail": "Addison is still working on the previous message.",
            "status": 409,
            "activeMessageId": "msg_running",
        }
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "conversation_busy"
    assert envelope["error"]["data"] == {"activeMessageId": "msg_running"}
    # Not the generic credentials advice a 409 would otherwise get.
    assert "--on-busy queue" in envelope["fix"]
    assert "auth whoami" not in envelope["fix"]


def test_queue_full_keeps_the_limit() -> None:
    envelope = _problem_envelope(
        {"code": "conversation_queue_full", "detail": "Queue is full.", "limit": 5}
    )
    assert envelope["error"]["data"] == {"limit": 5}
    assert "queue-list" in envelope["fix"]


def test_already_dispatched_withdrawal_points_at_the_bound_reply() -> None:
    envelope = _problem_envelope(
        {
            "code": "queued_message_already_dispatched",
            "detail": "This queued message already started.",
            "messageId": "msg_7",
            "userMessageId": "msg_6",
        }
    )
    assert envelope["error"]["data"] == {"messageId": "msg_7", "userMessageId": "msg_6"}
    assert "chats events" in envelope["fix"]


def test_non_queue_errors_keep_their_existing_envelope() -> None:
    envelope = _problem_envelope(
        {"code": "unauthenticated", "detail": "Token rejected."}, status=401
    )
    assert "error" in envelope and "data" not in envelope["error"]
    assert "auth login" in envelope["fix"]
