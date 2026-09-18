"""Unit tests for sum_cli.streaming (no CLI)."""

from __future__ import annotations

from sum_cli.streaming import (
    _payload_from_data,
    map_public_event,
    parse_sse_frame,
    stream_sse_response,
)


class _FakeResponse:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def iter_text(self):
        yield from self._chunks


def test_parse_sse_frame_multiline_data_event_and_id() -> None:
    frame = "event: update\nid: evt-42\ndata: line1\ndata: line2\n"
    parsed = parse_sse_frame(frame)
    assert parsed == {"event": "update", "id": "evt-42", "data": "line1\nline2"}


def test_parse_sse_frame_empty_returns_none() -> None:
    assert parse_sse_frame("") is None
    assert parse_sse_frame(": keepalive\n") is None


def test_map_public_event_message_delta() -> None:
    ndjson_type, fields = map_public_event("message.delta", {"text": "hello"})
    assert ndjson_type == "progress"
    assert fields == {"name": "message", "message": "hello"}


def test_map_public_event_message_delta_uses_delta_field() -> None:
    ndjson_type, fields = map_public_event("message.delta", {"delta": "chunk"})
    assert ndjson_type == "progress"
    assert fields == {"name": "message", "message": "chunk"}


def test_payload_from_data_parses_json_object() -> None:
    assert _payload_from_data('{"foo": 1, "bar": "baz"}') == {"foo": 1, "bar": "baz"}


def test_payload_from_data_raw_fallback() -> None:
    assert _payload_from_data("not-json") == {"raw": "not-json"}


def test_map_public_event_tool_started() -> None:
    ndjson_type, fields = map_public_event("tool.started", {"tool": "search"})
    assert ndjson_type == "step"
    assert fields["status"] == "started"


def test_map_public_event_tool_input_is_started() -> None:
    ndjson_type, fields = map_public_event("tool.input", {"name": "sql"})
    assert ndjson_type == "step"
    assert fields["status"] == "started"


def test_map_public_event_tool_completed() -> None:
    ndjson_type, fields = map_public_event("tool.completed", {"tool": "search"})
    assert ndjson_type == "step"
    assert fields["status"] == "completed"


def test_stream_error_event_carries_structured_data_not_stringified_dict() -> None:
    """DevX feedback #3: error payload must be structured JSON, not a Python repr."""
    frame = (
        "event: error\n"
        'data: {"code": "upstream_api_error", '
        '"message": "API Error: 500", '
        '"data": {"code": "upstream_api_error", "message": "API Error: 500"}}\n\n'
    )
    terminal = stream_sse_response(_FakeResponse([frame]), silent=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "upstream_api_error"
    # message is the real string, never a stringified dict
    assert terminal["error"]["message"] == "API Error: 500"
    assert "{'" not in terminal["error"]["message"]
    # structured payload is preserved
    assert terminal["error"]["data"]["code"] == "upstream_api_error"


def test_stream_done_event_returns_success_terminal() -> None:
    frame = 'event: done\ndata: {"messageId": "msg-1", "content": "hi"}\n\n'
    terminal = stream_sse_response(
        _FakeResponse([frame]),
        result_builder=lambda p, t: {"payload": p, "text": t},
        silent=True,
    )
    assert terminal["ok"] is True
    assert terminal["result"]["payload"]["content"] == "hi"


# ---------------------------------------------------------------------------
# Durable queue: waiting is not completion.
#
# Public sum-api frames nest their fields under {"type","sequence","data"}; the
# older flat-payload tests above still pass because `event_data` only unwraps a
# frame that actually carries the envelope.
# ---------------------------------------------------------------------------


def _frame(event: str, data: dict, sequence: int = 0) -> str:
    import json as _json

    body = _json.dumps({"type": event, "sequence": sequence, "data": data})
    return f"event: {event}\nid: {sequence}\ndata: {body}\n\n"


def test_event_data_unwraps_public_envelope_but_leaves_flat_payloads() -> None:
    from sum_cli.streaming import event_data

    assert event_data({"type": "done", "sequence": 3, "data": {"content": "hi"}}) == {
        "content": "hi"
    }
    assert event_data({"content": "hi"}) == {"content": "hi"}


def test_queued_status_is_recorded_without_being_a_terminal() -> None:
    from sum_cli.streaming import QueueObservation

    state = QueueObservation()
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_1", "position": 2}),
        _frame("done", {"messageId": "msg_9", "content": "answer"}, sequence=4),
    ]
    terminal = stream_sse_response(
        _FakeResponse(frames),
        result_builder=lambda p, t: {"payload": p},
        silent=True,
        queue_state=state,
    )

    assert state.queued_message_id == "qt_1"
    assert state.position == 2
    assert state.held is False
    assert terminal["ok"] is True
    assert terminal["result"]["payload"]["messageId"] == "msg_9"


def test_held_status_is_reported_as_held() -> None:
    from sum_cli.streaming import QueueObservation

    state = QueueObservation()
    stream_sse_response(
        _FakeResponse([_frame("status", {"message": "held", "queuedMessageId": "qt_2"})]),
        silent=True,
        queue_state=state,
    )
    assert state.held is True


def test_eof_while_queued_is_incomplete_not_success() -> None:
    """A stream that ends mid-wait has not delivered an answer. Never report ok."""
    frames = [_frame("status", {"message": "queued", "queuedMessageId": "qt_3", "position": 1})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)

    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_3"
    assert "queue-show" in terminal["fix"]


def test_eof_after_a_heartbeat_while_queued_is_still_incomplete() -> None:
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_4"}),
        _frame("heartbeat", {}, sequence=1),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"


def test_eof_without_a_queue_wait_keeps_the_existing_success_terminal() -> None:
    frames = [_frame("message.delta", {"text": "partial"})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is True
    assert terminal["result"]["text"] == "partial"


def test_queue_wait_timeout_error_preserves_the_recovery_id() -> None:
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_5"}),
        _frame(
            "error",
            {
                "code": "queue_wait_timeout",
                "message": "Timed out watching the queued message.",
                "queuedMessageId": "qt_5",
            },
            sequence=9,
        ),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "queue_wait_timeout"
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_5"


def test_withdrawn_queued_message_is_an_error_terminal() -> None:
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_6"}),
        _frame(
            "error",
            {
                "code": "queued_message_withdrawn",
                "message": "The queued message was withdrawn before it ran.",
                "queuedMessageId": "qt_6",
            },
            sequence=2,
        ),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "queued_message_withdrawn"


def test_queue_updated_advances_the_observed_position() -> None:
    from sum_cli.streaming import QueueObservation

    state = QueueObservation()
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_7", "position": 3}),
        _frame("queue.updated", {"position": 1, "queuedMessageId": "qt_7"}, sequence=1),
        _frame("done", {"messageId": "msg_1"}, sequence=2),
    ]
    stream_sse_response(_FakeResponse(frames), silent=True, queue_state=state)
    assert state.position == 1


def test_nested_message_delta_text_is_accumulated() -> None:
    frames = [
        _frame("message.delta", {"text": "Hello "}),
        _frame("message.delta", {"text": "world"}, sequence=1),
        _frame("done", {"messageId": "msg_1"}, sequence=2),
    ]
    terminal = stream_sse_response(
        _FakeResponse(frames),
        result_builder=lambda p, t: {"text": t},
        silent=True,
    )
    assert terminal["result"]["text"] == "Hello world"


# ---------------------------------------------------------------------------
# Interrupted waits. A dropped connection is not a dropped message: the receipt
# and the idempotency key must survive, and the advice must never be "retry",
# which for `chats reply` mints a new key and asks the same question twice.
# ---------------------------------------------------------------------------


class _FailingResponse:
    """Yields frames, then raises inside iter_text the way httpx does mid-stream."""

    def __init__(self, frames: list[str], exc: Exception) -> None:
        self._frames = frames
        self._exc = exc

    def iter_text(self):
        yield from self._frames
        raise self._exc


def test_transport_error_while_queued_keeps_the_receipt_and_the_key() -> None:
    import httpx as _httpx

    from sum_cli.streaming import QueueObservation

    state = QueueObservation(idempotency_key="send-42")
    terminal = stream_sse_response(
        _FailingResponse(
            [_frame("status", {"message": "queued", "queuedMessageId": "qt_8", "position": 2})],
            _httpx.ReadTimeout("The read operation timed out"),
        ),
        silent=True,
        queue_state=state,
    )

    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_8"
    assert terminal["error"]["data"]["idempotencyKey"] == "send-42"
    # The old generic advice would have caused a duplicate send.
    assert "retry the stream command" not in terminal["fix"]
    assert "send-42" in terminal["fix"]
    assert "do not re-send" in terminal["fix"].casefold()


def test_transport_error_without_a_queue_wait_keeps_the_generic_stream_error() -> None:
    import httpx as _httpx

    terminal = stream_sse_response(
        _FailingResponse([], _httpx.ConnectError("boom")), silent=True
    )
    assert terminal["error"]["code"] == "STREAM_ERROR"
    assert "retry the stream command" in terminal["fix"]


def test_eof_after_dispatch_reports_the_bound_reply_not_a_phantom_wait() -> None:
    """Once the turn starts, the message is running — withdrawing it would 409.

    `message.saved` is mapped by sum-api but has no producer in agent-service today,
    so in practice the bound id is usually absent and the fix falls back to
    `queue-show` (covered by the delta-only case below). This pins the behavior for
    when the event is emitted, and the id handling is shared with that fallback.
    """
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_9", "position": 1}),
        _frame("message.saved", {"messageId": "msg_20"}, sequence=1),
        _frame("message.delta", {"text": "half an answ"}, sequence=2),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)

    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "QUEUE_STREAM_INCOMPLETE"
    assert terminal["error"]["data"]["messageId"] == "msg_20"
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_9"
    # The partial answer is evidence, not garbage; and withdrawal is the wrong advice.
    assert terminal["error"]["data"]["text"] == "half an answ"
    assert "queue-withdraw" not in terminal["fix"]
    assert "chats events" in terminal["fix"]


def test_transport_error_after_dispatch_also_points_at_the_bound_message() -> None:
    import httpx as _httpx

    terminal = stream_sse_response(
        _FailingResponse(
            [
                _frame("status", {"message": "queued", "queuedMessageId": "qt_10"}),
                _frame("message.delta", {"text": "x"}, sequence=1),
            ],
            _httpx.ReadTimeout("timed out"),
        ),
        silent=True,
    )
    assert terminal["error"]["code"] == "QUEUE_STREAM_INCOMPLETE"
    assert "Do not re-send" in terminal["fix"]


def test_held_eof_offers_resume() -> None:
    frames = [_frame("status", {"message": "held", "queuedMessageId": "qt_11"})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"
    assert terminal["error"]["data"]["held"] is True
    assert "queue-resume" in terminal["fix"]


# ---------------------------------------------------------------------------
# The idempotency key dedupes ANY accepted reply, not only a queued one, so the
# "don't re-send" contract starts at the POST — not at the first queue receipt.
# ---------------------------------------------------------------------------


def test_transport_error_before_any_receipt_still_offers_the_key_not_a_retry() -> None:
    """The common case: the connection dies before (or without) a queue status frame."""
    import httpx as _httpx

    from sum_cli.streaming import QueueObservation

    state = QueueObservation(idempotency_key="send-7")
    terminal = stream_sse_response(
        _FailingResponse([], _httpx.ReadTimeout("read timed out")),
        silent=True,
        queue_state=state,
    )

    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "STREAM_INCOMPLETE"
    assert terminal["error"]["data"]["idempotencyKey"] == "send-7"
    assert "retry the stream command" not in terminal["fix"]
    assert "send-7" in terminal["fix"]


def test_eof_before_any_terminal_is_incomplete_when_the_route_always_terminates() -> None:
    frames = [_frame("message.delta", {"text": "half"})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True, require_terminal=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "STREAM_INCOMPLETE"
    assert terminal["error"]["data"]["text"] == "half"


def test_permissive_endpoints_keep_the_success_fallback() -> None:
    """reports/grid streams are not covered by the chats always-terminal contract."""
    frames = [_frame("message.delta", {"text": "partial"})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is True
    assert terminal["result"]["text"] == "partial"


def test_queued_status_without_an_id_still_counts_as_waiting() -> None:
    """A receipt-less status must not silently degrade into 'never queued' success."""
    frames = [_frame("status", {"message": "queued", "position": 1})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"


def test_done_with_cancelled_status_is_not_success() -> None:
    """`chats cancel` makes this reachable: a stopped turn has no complete answer."""
    frames = [
        _frame("message.delta", {"text": "half an ans"}),
        _frame("done", {"messageId": "msg_30", "status": "cancelled"}, sequence=1),
    ]
    terminal = stream_sse_response(
        _FakeResponse(frames), result_builder=lambda p, t: {"text": t}, silent=True
    )
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "REPLY_CANCELLED"
    assert terminal["error"]["data"]["messageId"] == "msg_30"
    assert terminal["error"]["data"]["text"] == "half an ans"


def test_done_with_error_status_is_not_success() -> None:
    frames = [_frame("done", {"messageId": "msg_31", "status": "error"})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "REPLY_ERROR"


def test_done_with_complete_status_is_success() -> None:
    frames = [_frame("done", {"messageId": "msg_32", "status": "complete"})]
    terminal = stream_sse_response(
        _FakeResponse(frames), result_builder=lambda p, t: {"payload": p}, silent=True
    )
    assert terminal["ok"] is True
    assert terminal["result"]["payload"]["messageId"] == "msg_32"


def test_done_without_a_status_field_is_success() -> None:
    """Older/minimal done frames must keep working."""
    terminal = stream_sse_response(
        _FakeResponse([_frame("done", {"messageId": "msg_33"})]), silent=True
    )
    assert terminal["ok"] is True


# ---------------------------------------------------------------------------
# The server's own error terminal. sum-api projects an error frame to exactly
# {code, message} (public_stream_payload), so the receipt and the key can only
# come from what this stream observed — and without them the advice degrades to
# "retry", which re-sends a turn the server already accepted.
# ---------------------------------------------------------------------------


def test_server_error_terminal_keeps_the_key_and_the_partial_answer() -> None:
    from sum_cli.streaming import QueueObservation

    state = QueueObservation(idempotency_key="send-88")
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_40", "position": 1}),
        _frame("message.delta", {"text": "partial"}, sequence=1),
        _frame(
            "error",
            {"code": "queue_wait_timeout", "message": "Timed out watching the queued message."},
            sequence=2,
        ),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True, queue_state=state)

    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "queue_wait_timeout"
    assert terminal["error"]["data"]["idempotencyKey"] == "send-88"
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_40"
    assert terminal["error"]["data"]["text"] == "partial"
    assert "retry" not in terminal["fix"].casefold()
    assert "send-88" in terminal["fix"]


def test_server_error_frame_fields_win_over_observed_ones() -> None:
    """The frame is authoritative for anything it actually carries."""
    from sum_cli.streaming import QueueObservation

    state = QueueObservation(queued_message_id="qt_local", idempotency_key="k")
    frames = [
        _frame("error", {"code": "x", "message": "m", "queuedMessageId": "qt_from_server"})
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True, queue_state=state)
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_from_server"


def test_plain_stream_error_without_a_key_keeps_the_simple_hint() -> None:
    frames = [_frame("error", {"code": "upstream_error", "message": "boom"})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["error"]["code"] == "upstream_error"
    assert "idempotency-key" not in terminal["fix"]


# ---------------------------------------------------------------------------
# sum-api appends `done` with an EMPTY data object when the upstream stream
# ended without its own terminal, so truncation arrives wearing a success
# event's name. Every genuine done carries a message id and a status.
# ---------------------------------------------------------------------------


def test_identityless_done_is_truncation_not_success() -> None:
    frames = [
        _frame("message.delta", {"text": "half"}),
        _frame("done", {}, sequence=1),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True, require_terminal=True)
    assert terminal["ok"] is False
    assert terminal["error"]["code"] == "STREAM_INCOMPLETE"
    assert terminal["error"]["data"]["text"] == "half"


def test_identityless_done_while_queued_reports_the_receipt() -> None:
    frames = [
        _frame("status", {"message": "queued", "queuedMessageId": "qt_41"}),
        _frame("done", {}, sequence=1),
    ]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True, require_terminal=True)
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"
    assert terminal["error"]["data"]["queuedMessageId"] == "qt_41"


def test_identityless_done_stays_permissive_for_uncontracted_endpoints() -> None:
    """reports/grid keep the old fallback; only the chats routes require a terminal."""
    frames = [_frame("message.delta", {"text": "half"}), _frame("done", {}, sequence=1)]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["ok"] is True


def test_interrupted_plain_reply_offers_the_message_it_saw() -> None:
    """No queue involved: the bound id still beats advising a re-POST."""
    import httpx as _httpx

    from sum_cli.streaming import QueueObservation

    terminal = stream_sse_response(
        _FailingResponse(
            [_frame("message.saved", {"messageId": "msg_50"})],
            _httpx.ReadTimeout("timed out"),
        ),
        silent=True,
        queue_state=QueueObservation(idempotency_key="send-9"),
    )
    assert terminal["error"]["code"] == "STREAM_INCOMPLETE"
    assert "msg_50" in terminal["fix"]


def test_receiptless_wait_never_prints_a_placeholder_command() -> None:
    frames = [_frame("status", {"message": "queued", "position": 1})]
    terminal = stream_sse_response(_FakeResponse(frames), silent=True)
    assert terminal["error"]["code"] == "QUEUE_INCOMPLETE"
    assert "--queued-message it" not in terminal["fix"]
    assert "queued message the queued message" not in terminal["error"]["message"]
    assert "queue-list" in terminal["fix"]
