"""`sumcli chats feedback` — request shape and client-side validation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.resources.chats import _DETAILS_MAX_LEN

runner = CliRunner()

_FEEDBACK_RESPONSE = {
    "data": {
        "id": "fb_1",
        "message_id": "msg_1",
        "rating": "thumbs_down",
        "reason": "incorrect_info",
        "details": "Wrong revenue figure.",
        "created_at": "2026-08-02T00:00:00Z",
    }
}

_BASE_ARGS = [
    "chats",
    "feedback",
    "--project",
    "proj_1",
    "--chat",
    "chat_1",
    "--message",
    "msg_1",
]


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _invoke(args: list[str], response: dict | None = _FEEDBACK_RESPONSE):
    mock_client = MagicMock()
    mock_client.request.return_value = response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    with patch("sum_cli.resources.chats.api_client", return_value=mock_cm):
        result = runner.invoke(app, args)
    return result, mock_client


def test_feedback_posts_full_payload() -> None:
    result, client = _invoke(
        [
            *_BASE_ARGS,
            "--rating",
            "thumbs_down",
            "--reason",
            "incorrect_info",
            "--details",
            "Wrong revenue figure.",
        ]
    )

    assert result.exit_code == 0, result.stdout
    method, path = client.request.call_args.args
    assert method == "POST"
    assert path == "/v1/projects/proj_1/conversations/chat_1/messages/msg_1/feedback"
    assert client.request.call_args.kwargs["json"] == {
        "rating": "thumbs_down",
        "reason": "incorrect_info",
        "details": "Wrong revenue figure.",
    }

    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["feedback"]["id"] == "fb_1"
    assert body["result"]["chat_id"] == "chat_1"
    assert body["result"]["message_id"] == "msg_1"
    assert body["result"]["project_id"] == "proj_1"


def test_feedback_omits_unset_optional_fields() -> None:
    result, client = _invoke([*_BASE_ARGS, "--rating", "thumbs_up"])

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.kwargs["json"] == {"rating": "thumbs_up"}


def test_feedback_requires_rating() -> None:
    result, client = _invoke(_BASE_ARGS)

    assert result.exit_code != 0
    client.request.assert_not_called()


def test_feedback_rejects_unknown_rating() -> None:
    result, client = _invoke([*_BASE_ARGS, "--rating", "thumbs_sideways"])

    assert result.exit_code != 0
    client.request.assert_not_called()


def test_feedback_rejects_unknown_reason() -> None:
    result, client = _invoke([*_BASE_ARGS, "--rating", "thumbs_down", "--reason", "nope"])

    assert result.exit_code != 0
    client.request.assert_not_called()


def test_feedback_rejects_overlong_details() -> None:
    result, client = _invoke(
        [*_BASE_ARGS, "--rating", "thumbs_down", "--details", "x" * (_DETAILS_MAX_LEN + 1)]
    )

    assert result.exit_code != 0
    client.request.assert_not_called()
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "DETAILS_TOO_LONG"


def test_feedback_accepts_details_at_limit() -> None:
    result, client = _invoke(
        [*_BASE_ARGS, "--rating", "thumbs_down", "--details", "x" * _DETAILS_MAX_LEN]
    )

    assert result.exit_code == 0, result.stdout
    assert len(client.request.call_args.kwargs["json"]["details"]) == _DETAILS_MAX_LEN


def test_overlong_details_reported_before_missing_project() -> None:
    """Pure input validation must not need a resolved project to report.

    Without this ordering a user with no default project sees NO_PROJECT first,
    fixes it, then re-runs and only then learns the details were too long.
    """
    result, client = _invoke(
        [
            "chats",
            "feedback",
            "--chat",
            "chat_1",
            "--message",
            "msg_1",
            "--rating",
            "thumbs_down",
            "--details",
            "x" * (_DETAILS_MAX_LEN + 1),
        ]
    )

    assert result.exit_code != 0
    client.request.assert_not_called()
    assert json.loads(result.stdout)["error"]["code"] == "DETAILS_TOO_LONG"
