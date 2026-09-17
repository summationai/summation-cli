"""`sumcli chats` durable-queue surface — request shapes and refusal behavior.

Covers the public conversation-queue contract sum-api exposes (see the bundled
OpenAPI snapshot): ``on_busy``/``idempotency_key`` on reply, the four queue
management commands, and the confirm-gated cancel. Every test asserts the exact
method/path/params/body the CLI puts on the wire, because those are the parts a
snapshot refresh cannot catch on its own.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()

_QUEUE_ITEM = {
    "id": "qt_1",
    "position": 2,
    "state": "queued",
    "message": "Compare Q3",
    "displayContent": None,
    "model": None,
    "effort": None,
    "createdAt": "2026-09-17T00:00:00Z",
    "behindMessageId": "msg_running",
    "createdByUserId": "usr_1",
    "messageId": None,
    "userMessageId": None,
    "failureCode": None,
    "failureDetail": None,
}


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _invoke(args: list[str], response: object = None):
    mock_client = MagicMock()
    mock_client.request.return_value = response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    with patch("sum_cli.resources.chats.api_client", return_value=mock_cm):
        result = runner.invoke(app, args)
    return result, mock_client


# ---------------------------------------------------------------------------
# reply: on_busy + idempotency key
# ---------------------------------------------------------------------------


def _reply(args: list[str]):
    """Invoke ``chats reply`` with the streaming layer stubbed to a done terminal."""
    mock_client = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    captured: dict = {}

    def fake_post(client, method, path, *, wait, follow, json=None, result_builder=None, **kw):
        from sum_cli.stream_options import StreamPostResult

        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        captured["queue_state"] = kw.get("queue_state")
        return StreamPostResult(streamed=False, body={"messageId": "msg_2"})

    with (
        patch("sum_cli.resources.chats.api_client", return_value=mock_cm),
        patch("sum_cli.resources.chats.post_with_wait_follow", side_effect=fake_post),
    ):
        result = runner.invoke(app, args)
    return result, captured


_REPLY_ARGS = ["chats", "reply", "--project", "proj_1", "--chat", "chat_1", "-m", "Compare Q3"]


def test_reply_defaults_to_queue_and_generates_an_idempotency_key() -> None:
    result, captured = _reply(_REPLY_ARGS)

    assert result.exit_code == 0, result.stdout
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/projects/proj_1/conversations/chat_1/messages"
    body = captured["json"]
    assert body["message"] == "Compare Q3"
    # Default is the durable behavior, sent explicitly rather than relying on the
    # server default, so an older deployment cannot silently reject instead.
    assert body["on_busy"] == "queue"
    assert isinstance(body["idempotency_key"], str) and body["idempotency_key"]


def test_reply_on_busy_reject_is_sent_verbatim() -> None:
    _, captured = _reply([*_REPLY_ARGS, "--on-busy", "reject"])
    assert captured["json"]["on_busy"] == "reject"


def test_reply_rejects_an_unsupported_on_busy_mode() -> None:
    """Only reject/queue exist; steer/interrupt must fail at parse time, not 422."""
    result, _ = _reply([*_REPLY_ARGS, "--on-busy", "steer"])
    assert result.exit_code == 2


def test_reply_preserves_an_explicit_idempotency_key() -> None:
    _, captured = _reply([*_REPLY_ARGS, "--idempotency-key", "send-42"])
    assert captured["json"]["idempotency_key"] == "send-42"


def test_reply_generates_a_distinct_key_per_invocation() -> None:
    _, first = _reply(_REPLY_ARGS)
    _, second = _reply(_REPLY_ARGS)
    assert first["json"]["idempotency_key"] != second["json"]["idempotency_key"]


def test_reply_reports_a_queued_message_id_in_the_envelope() -> None:
    """A reply that waited must surface the receipt id, not just the final text."""
    from sum_cli.streaming import QueueObservation

    def fake_post(client, method, path, *, wait, follow, json=None, result_builder=None, **kw):
        from sum_cli.stream_options import StreamPostResult

        state = kw["queue_state"]
        assert isinstance(state, QueueObservation)
        state.queued_message_id = "qt_9"
        state.position = 1
        return StreamPostResult(streamed=False, body={"messageId": "msg_2"})

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = MagicMock()
    mock_cm.__exit__.return_value = None
    with (
        patch("sum_cli.resources.chats.api_client", return_value=mock_cm),
        patch("sum_cli.resources.chats.post_with_wait_follow", side_effect=fake_post),
    ):
        result = runner.invoke(app, _REPLY_ARGS)

    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["result"]["queued_message"]["queued_message_id"] == "qt_9"


# ---------------------------------------------------------------------------
# queue-list / queue-show
# ---------------------------------------------------------------------------


def test_queue_list_calls_the_public_queue_route() -> None:
    result, client = _invoke(
        ["chats", "queue-list", "--project", "proj_1", "--chat", "chat_1"],
        {"data": {"items": [_QUEUE_ITEM], "held": True, "revision": 7}},
    )

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.args == (
        "GET",
        "/v1/projects/proj_1/conversations/chat_1/queue",
    )
    body = json.loads(result.stdout)
    assert body["result"]["queued_messages"][0]["id"] == "qt_1"
    assert body["result"]["held"] is True
    assert body["result"]["revision"] == 7


def test_queue_list_held_offers_resume_as_the_next_action() -> None:
    result, _ = _invoke(
        ["chats", "queue-list", "--project", "proj_1", "--chat", "chat_1"],
        {"data": {"items": [_QUEUE_ITEM], "held": True, "revision": 7}},
    )
    commands = [a["command"] for a in json.loads(result.stdout)["next_actions"]]
    assert any("queue-resume" in c for c in commands)


def test_queue_show_addresses_one_item() -> None:
    result, client = _invoke(
        [
            "chats",
            "queue-show",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--queued-message",
            "qt_1",
        ],
        {"data": _QUEUE_ITEM},
    )

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.args == (
        "GET",
        "/v1/projects/proj_1/conversations/chat_1/queue/qt_1",
    )
    assert json.loads(result.stdout)["result"]["queued_message"]["state"] == "queued"


def test_queue_show_of_a_dispatched_item_points_at_its_events() -> None:
    """A queued message can finish before you poll; show must hand back its reply."""
    dispatched = {**_QUEUE_ITEM, "state": "dispatched", "messageId": "msg_7", "position": 0}
    result, _ = _invoke(
        [
            "chats",
            "queue-show",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--queued-message",
            "qt_1",
        ],
        {"data": dispatched},
    )
    commands = [a["command"] for a in json.loads(result.stdout)["next_actions"]]
    assert any("chats events" in c for c in commands)


def test_queue_show_of_a_failed_item_exits_nonzero_with_its_failure_code() -> None:
    failed = {
        **_QUEUE_ITEM,
        "state": "failed",
        "position": 0,
        "failureCode": "attachment_deleted",
        "failureDetail": "A referenced file was deleted before the turn ran.",
    }
    result, _ = _invoke(
        [
            "chats",
            "queue-show",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--queued-message",
            "qt_1",
        ],
        {"data": failed},
    )
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "QUEUED_MESSAGE_FAILED"
    assert body["error"]["data"]["failure_code"] == "attachment_deleted"


def test_queue_show_of_a_withdrawn_item_exits_nonzero() -> None:
    withdrawn = {**_QUEUE_ITEM, "state": "withdrawn", "position": 0}
    result, _ = _invoke(
        [
            "chats",
            "queue-show",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--queued-message",
            "qt_1",
        ],
        {"data": withdrawn},
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "QUEUED_MESSAGE_WITHDRAWN"


# ---------------------------------------------------------------------------
# queue-withdraw
# ---------------------------------------------------------------------------


def test_queue_withdraw_requires_confirm_and_sends_no_request() -> None:
    result, client = _invoke(
        [
            "chats",
            "queue-withdraw",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--queued-message",
            "qt_1",
        ]
    )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_queue_withdraw_deletes_with_confirm_true() -> None:
    result, client = _invoke(
        [
            "chats",
            "queue-withdraw",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--queued-message",
            "qt_1",
            "--confirm",
        ],
        {"data": {"id": "qt_1", "state": "withdrawn", "revision": 8}},
    )

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.args == (
        "DELETE",
        "/v1/projects/proj_1/conversations/chat_1/queue/qt_1",
    )
    assert client.request.call_args.kwargs["params"] == {"confirm": True}
    body = json.loads(result.stdout)
    assert body["result"]["queued_message"]["state"] == "withdrawn"


# ---------------------------------------------------------------------------
# queue-resume
# ---------------------------------------------------------------------------


def test_queue_resume_posts_to_the_resume_route() -> None:
    result, client = _invoke(
        ["chats", "queue-resume", "--project", "proj_1", "--chat", "chat_1"],
        {
            "data": {
                "held": False,
                "revision": 9,
                "activeMessageId": "msg_running",
                "queuedTurns": [_QUEUE_ITEM],
            }
        },
    )

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.args == (
        "POST",
        "/v1/projects/proj_1/conversations/chat_1/queue/resume",
    )
    body = json.loads(result.stdout)
    assert body["result"]["held"] is False
    assert body["result"]["revision"] == 9
    assert body["result"]["queued_messages"][0]["id"] == "qt_1"


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


def test_cancel_requires_confirm() -> None:
    result, client = _invoke(
        ["chats", "cancel", "--project", "proj_1", "--chat", "chat_1", "--message", "msg_1"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_cancel_posts_with_confirm_and_reports_the_hold() -> None:
    result, client = _invoke(
        [
            "chats",
            "cancel",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--message",
            "msg_1",
            "--confirm",
        ],
        {"data": {"messageId": "msg_1", "status": "canceled", "queueHeld": True}},
    )

    assert result.exit_code == 0, result.stdout
    assert client.request.call_args.args == (
        "POST",
        "/v1/projects/proj_1/conversations/chat_1/messages/msg_1/cancel",
    )
    assert client.request.call_args.kwargs["params"] == {"confirm": True}
    body = json.loads(result.stdout)
    assert body["result"]["cancel"]["queueHeld"] is True
    # A Stop holds the queue; the caller needs the way back.
    commands = [a["command"] for a in body["next_actions"]]
    assert any("queue-resume" in c for c in commands)
