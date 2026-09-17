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
