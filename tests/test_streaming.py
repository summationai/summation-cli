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
