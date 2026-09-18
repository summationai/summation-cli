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
        # Drive the real result_builder: the receipt rides in the terminal result so
        # that --follow's NDJSON carries it too, not just the non-streamed envelope.
        assert result_builder is not None
        return StreamPostResult(streamed=False, body=result_builder({"messageId": "msg_2"}, ""))

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
    assert body["error"]["data"]["failureCode"] == "attachment_deleted"
    assert body["error"]["data"]["queuedMessageId"] == "qt_1"


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


# ---------------------------------------------------------------------------
# Wire-shape regression: the real public SSE envelope, end to end.
#
# sum-api serializes every public event as {"type","sequence","data":{...}}
# (sum_api/agent_client.py::public_agent_event, deployed today). Before this PR
# the CLI read `text`/`messageId` off the envelope instead of its `data`, so an
# immediate reply accumulated no text and surfaced the raw envelope as its
# payload — contradicting the documented "returns messageId in the terminal
# payload". These drive the command through httpx-shaped frames so a revert to
# the flat read fails here, not only in the streaming unit tests.
# ---------------------------------------------------------------------------


def _wire_frame(event: str, data: dict, sequence: int) -> str:
    body = json.dumps({"type": event, "sequence": sequence, "data": data})
    return f"event: {event}\nid: {sequence}\ndata: {body}\n\n"


def _reply_over_wire(args: list[str], frames: list[str]):
    resp = MagicMock()
    resp.iter_text.return_value = iter(frames)
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = resp
    stream_cm.__exit__.return_value = None
    client = MagicMock()
    client.stream.return_value = stream_cm
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None
    with patch("sum_cli.resources.chats.api_client", return_value=cm):
        return runner.invoke(app, args), client


def test_immediate_reply_reports_text_and_message_id_from_the_real_envelope() -> None:
    result, client = _reply_over_wire(
        _REPLY_ARGS,
        [
            _wire_frame("message.delta", {"text": "Q3 "}, 0),
            _wire_frame("message.delta", {"text": "grew 4%."}, 1),
            _wire_frame(
                "done",
                {"messageId": "msg_5", "chatId": "chat_1", "status": "complete"},
                2,
            ),
        ],
    )

    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    message = body["result"]["message"]
    assert message["text"] == "Q3 grew 4%."
    assert message["payload"]["messageId"] == "msg_5"
    # An immediate reply never waited, so it must not claim a queue receipt.
    assert "queued_message" not in body["result"]
    # The body still carries the send options the queue contract needs.
    assert client.stream.call_args.kwargs["json"]["on_busy"] == "queue"


def test_queued_then_dispatched_reply_reports_both_receipt_and_answer() -> None:
    """The full queued timeline: status, heartbeat, position advance, then the reply."""
    result, _ = _reply_over_wire(
        _REPLY_ARGS,
        [
            _wire_frame(
                "status",
                {
                    "message": "queued",
                    "position": 2,
                    "behindMessageId": "msg_running",
                    "queuedMessageId": "qt_11",
                },
                0,
            ),
            _wire_frame("heartbeat", {}, 1),
            _wire_frame("queue.updated", {"position": 1, "queuedMessageId": "qt_11"}, 2),
            _wire_frame("message.delta", {"text": "done waiting"}, 3),
            _wire_frame("done", {"messageId": "msg_12", "status": "complete"}, 4),
        ],
    )

    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["result"]["queued_message"]["queued_message_id"] == "qt_11"
    assert body["result"]["queued_message"]["position"] == 1
    assert body["result"]["message"]["payload"]["messageId"] == "msg_12"
    assert body["result"]["message"]["text"] == "done waiting"


def test_reply_that_never_dispatches_fails_with_the_receipt() -> None:
    result, _ = _reply_over_wire(
        _REPLY_ARGS,
        [
            _wire_frame(
                "status", {"message": "queued", "position": 1, "queuedMessageId": "qt_13"}, 0
            ),
            _wire_frame("heartbeat", {}, 1),
        ],
    )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "QUEUE_INCOMPLETE"
    assert body["error"]["data"]["queuedMessageId"] == "qt_13"


# ---------------------------------------------------------------------------
# Shape handling: a response the CLI could not read must never look like a
# healthy empty queue (SUM-5882), but an optional-and-absent list key is a real
# zero. Both matter here: "no waiting messages" is what a caller acts on when
# deciding whether to send again.
# ---------------------------------------------------------------------------


def test_queue_list_refuses_a_response_with_no_data_object() -> None:
    result, _ = _invoke(
        ["chats", "queue-list", "--project", "proj_1", "--chat", "chat_1"],
        {"queue": {"items": []}},
    )
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "UNEXPECTED_SHAPE"


def test_queue_list_treats_an_absent_items_key_as_an_empty_queue() -> None:
    """`items` has a default in the contract, so absent-with-siblings is a real zero."""
    result, _ = _invoke(
        ["chats", "queue-list", "--project", "proj_1", "--chat", "chat_1"],
        {"data": {"held": False, "revision": 3}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["result"]["queued_messages"] == []
    assert body["result"]["revision"] == 3


def test_queue_list_still_refuses_an_unrecognized_data_shape() -> None:
    result, _ = _invoke(
        ["chats", "queue-list", "--project", "proj_1", "--chat", "chat_1"],
        {"data": {"messages": [_QUEUE_ITEM]}},
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "UNEXPECTED_SHAPE"


def test_queue_resume_treats_an_absent_queued_turns_key_as_empty() -> None:
    result, _ = _invoke(
        ["chats", "queue-resume", "--project", "proj_1", "--chat", "chat_1"],
        {"data": {"held": False, "revision": 4}},
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["queued_messages"] == []


def test_queue_show_of_a_dispatching_item_does_not_offer_withdrawal() -> None:
    """Withdrawal loses that race with a 409; re-reading is the only useful step."""
    dispatching = {**_QUEUE_ITEM, "state": "dispatching", "messageId": None}
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
        {"data": dispatching},
    )
    assert result.exit_code == 0, result.stdout
    commands = [a["command"] for a in json.loads(result.stdout)["next_actions"]]
    assert not any("queue-withdraw" in c for c in commands)
    assert any("queue-show" in c for c in commands)


def test_cancel_of_an_unknown_message_fails_instead_of_claiming_success() -> None:
    """ok:true here lets an agent send the next turn into the reply it thought it killed."""
    result, _ = _invoke(
        [
            "chats",
            "cancel",
            "--project",
            "proj_1",
            "--chat",
            "chat_1",
            "--message",
            "msg_bogus",
            "--confirm",
        ],
        {"data": {"messageId": "msg_bogus", "status": "not_found", "queueHeld": None}},
    )
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CANCEL_TARGET_NOT_FOUND"
    assert body["error"]["data"]["messageId"] == "msg_bogus"


def test_cancel_of_an_already_finished_reply_is_still_a_success() -> None:
    """Idempotent: the reply is genuinely over, which is what the caller wanted."""
    result, _ = _invoke(
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
        {"data": {"messageId": "msg_1", "status": "already_complete", "queueHeld": False}},
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["cancel"]["status"] == "already_complete"


def test_reply_reports_the_generated_idempotency_key_when_it_waits() -> None:
    """Recovery means replaying the same key, so the caller has to be told it."""
    result, _ = _reply_over_wire(
        _REPLY_ARGS,
        [
            _wire_frame(
                "status", {"message": "queued", "position": 1, "queuedMessageId": "qt_14"}, 0
            ),
            _wire_frame("done", {"messageId": "msg_15"}, 1),
        ],
    )
    assert result.exit_code == 0, result.stdout
    key = json.loads(result.stdout)["result"]["queued_message"]["idempotency_key"]
    assert isinstance(key, str) and key


def test_interrupted_queued_reply_prints_the_key_needed_to_resume_it() -> None:
    result, _ = _reply_over_wire(
        [*_REPLY_ARGS, "--idempotency-key", "send-99"],
        [
            _wire_frame(
                "status", {"message": "queued", "position": 1, "queuedMessageId": "qt_16"}, 0
            )
        ],
    )
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["data"]["idempotencyKey"] == "send-99"
    assert "send-99" in body["fix"]


def test_follow_mode_terminal_carries_the_queue_receipt() -> None:
    """Docs point agents at result.queued_message.idempotency_key in BOTH modes."""
    result, _ = _reply_over_wire(
        [*_REPLY_ARGS, "--follow", "--idempotency-key", "send-55"],
        [
            _wire_frame(
                "status", {"message": "queued", "position": 1, "queuedMessageId": "qt_20"}, 0
            ),
            _wire_frame("done", {"messageId": "msg_21", "status": "complete"}, 1),
        ],
    )

    assert result.exit_code == 0, result.stdout
    lines = [json.loads(line) for line in result.stdout.strip().split("\n")]
    terminal = lines[-1]
    assert terminal["type"] == "result"
    receipt = terminal["result"]["queued_message"]
    assert receipt["queued_message_id"] == "qt_20"
    assert receipt["idempotency_key"] == "send-55"


def test_reply_interrupted_before_any_receipt_reports_the_key() -> None:
    """The POST was accepted; a plain re-run would ask Addison the same thing twice."""
    result, _ = _reply_over_wire(
        [*_REPLY_ARGS, "--idempotency-key", "send-56"],
        [_wire_frame("message.delta", {"text": "started"}, 0)],
    )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "STREAM_INCOMPLETE"
    assert body["error"]["data"]["idempotencyKey"] == "send-56"
    assert body["error"]["data"]["text"] == "started"


def test_reply_cancelled_mid_answer_does_not_report_success() -> None:
    result, _ = _reply_over_wire(
        _REPLY_ARGS,
        [
            _wire_frame("message.delta", {"text": "partial"}, 0),
            _wire_frame("done", {"messageId": "msg_22", "status": "cancelled"}, 1),
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "REPLY_CANCELLED"


def test_reply_whose_stream_never_opens_reports_the_key_not_a_retry() -> None:
    """The send may already have landed: sum-api accepts before it streams."""
    import httpx

    client = MagicMock()
    client.stream.side_effect = httpx.ReadTimeout("timed out")
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None

    with patch("sum_cli.resources.chats.api_client", return_value=cm):
        result = runner.invoke(app, [*_REPLY_ARGS, "--idempotency-key", "send-78"])

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "STREAM_INCOMPLETE"
    assert body["error"]["data"]["idempotencyKey"] == "send-78"
    assert "may already have been accepted" in body["fix"]
    assert "Check that you are online" not in body["fix"]


@pytest.mark.parametrize("status", [502, 503, 504])
def test_reply_unconfirmed_by_a_sum_api_5xx_reports_the_key(status: int) -> None:
    """sum-api forwards the send, then fails to read the answer back.

    The turn may already be durable, so the default "check auth whoami" guidance is
    wrong twice over — and re-running would ask Addison the same question again.
    """
    from sum_cli.client import ApiError

    client = MagicMock()
    client.stream.side_effect = ApiError(
        status, {"code": "timeout", "detail": "A product service timed out.", "status": status}
    )
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None

    with patch("sum_cli.resources.chats.api_client", return_value=cm):
        result = runner.invoke(
            app, [*_REPLY_ARGS, "--idempotency-key", "send-79"], standalone_mode=False
        )

    from sum_cli.cli.main import _api_error_envelope

    envelope = _api_error_envelope(result.exception)
    assert envelope["error"]["data"]["idempotencyKey"] == "send-79"
    assert "send-79" in envelope["fix"]
    assert "auth whoami" not in envelope["fix"]


def test_reply_refused_with_4xx_keeps_the_generic_envelope() -> None:
    """A 409/429 is a refusal: that send did not land, so no replay advice."""
    from sum_cli.cli.main import _api_error_envelope
    from sum_cli.client import ApiError

    client = MagicMock()
    client.stream.side_effect = ApiError(
        409, {"code": "conversation_queue_full", "detail": "full", "limit": 5}
    )
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None

    with patch("sum_cli.resources.chats.api_client", return_value=cm):
        result = runner.invoke(
            app, [*_REPLY_ARGS, "--idempotency-key", "send-80"], standalone_mode=False
        )

    envelope = _api_error_envelope(result.exception)
    assert "idempotencyKey" not in envelope["error"]["data"]
    assert envelope["error"]["data"]["limit"] == 5
    assert "queue-list" in envelope["fix"]


def test_queue_show_of_a_dispatched_item_without_a_binding_does_not_offer_withdrawal() -> None:
    """Transient: claimed, but the id is not in this projection yet. Withdrawal 409s."""
    dispatched = {**_QUEUE_ITEM, "state": "dispatched", "messageId": None, "position": 0}
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
    assert result.exit_code == 0, result.stdout
    commands = [a["command"] for a in json.loads(result.stdout)["next_actions"]]
    assert not any("queue-withdraw" in c for c in commands)


def test_create_interrupted_after_the_post_does_not_advise_a_bare_retry() -> None:
    """chats create has no idempotency key, but its stream always terminates.

    A retry would buy a second chat and a second paid turn, so the unconfirmed
    wording applies even without a key to replay.
    """
    import httpx

    client = MagicMock()
    client.stream.side_effect = httpx.ReadTimeout("timed out")
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None

    with patch("sum_cli.resources.chats.api_client", return_value=cm):
        result = runner.invoke(
            app, ["chats", "create", "--project", "proj_1", "-m", "hello"]
        )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "STREAM_INCOMPLETE"
    assert "may already have been accepted" in body["fix"]
