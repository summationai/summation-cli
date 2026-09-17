"""Map sum-api public SSE streams to NDJSON lines."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from sum_cli.output import _current_command, err, ndjson, ok


def event_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an SSE frame's own fields, unwrapping the public envelope.

    sum-api serializes every public event as ``{"type", "sequence", "data"}``, so
    the fields a command cares about (``text``, ``messageId``, ``queuedMessageId``)
    live one level down. Older fixtures — and any surface that sends the fields
    flat — are left alone: the envelope is only unwrapped when ``type`` is present
    alongside a dict ``data``, which a flat payload never has.
    """
    inner = payload.get("data")
    if "type" in payload and isinstance(inner, dict):
        return inner
    return payload


@dataclass
class QueueObservation:
    """What a stream observed about a durably queued message.

    A queue-mode send answers with a ``status`` event carrying the queue receipt
    id long before (or instead of) an assistant message. Commands pass one of
    these in so they can report the receipt, and so the stream can tell a wait
    that was still pending at EOF apart from a completed answer.
    """

    queued_message_id: str | None = None
    position: int | None = None
    held: bool = False

    @property
    def waiting(self) -> bool:
        return self.queued_message_id is not None

    def as_result(self) -> dict[str, Any]:
        return {
            "queued_message_id": self.queued_message_id,
            "position": self.position,
            "held": self.held,
        }


def parse_sse_frame(raw_frame: str) -> dict[str, str] | None:
    event = "message"
    event_id = ""
    data_lines: list[str] = []
    for line in raw_frame.split("\n"):
        if not line or line.startswith(":"):
            continue
        if ":" in line:
            field, _, value = line.partition(":")
            value = value.lstrip()
        else:
            field, value = line, ""
        if field == "event":
            event = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)
    if not data_lines and event == "message":
        return None
    return {"event": event, "id": event_id, "data": "\n".join(data_lines)}


def _payload_from_data(data: str) -> dict[str, Any]:
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"raw": data}


def map_public_event(event_type: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if event_type == "message.delta":
        text = payload.get("text") or payload.get("delta") or ""
        return "progress", {"name": "message", "message": text}
    if event_type == "tool.started":
        return "step", {
            "name": payload.get("tool") or payload.get("name") or "tool",
            "status": "started",
        }
    if event_type == "tool.input":
        return "step", {
            "name": payload.get("tool") or payload.get("name") or "tool",
            "status": "started",
        }
    if event_type == "tool.completed":
        return "step", {
            "name": payload.get("tool") or payload.get("name") or "tool",
            "status": "completed",
        }
    if event_type == "error":
        return "log", {"level": "error", "message": payload.get("message", str(payload))}
    if event_type in ("status", "heartbeat"):
        return "log", {"level": "info", "message": payload.get("message") or event_type}
    if event_type == "done":
        return "log", {"level": "info", "message": "done"}
    return "log", {"level": "info", "message": json.dumps(payload)}


def stream_sse_response(
    resp: httpx.Response,
    *,
    raw_sse: bool = False,
    result_builder: Any = None,
    silent: bool = False,
    queue_state: QueueObservation | None = None,
) -> dict[str, Any]:
    """Consume an SSE httpx stream and return a terminal envelope dict.

    When `silent` is False, also emit a live NDJSON record per event.

    `queue_state` is filled in when the reply was durably queued rather than
    dispatched immediately; pass one to report the receipt id to the caller. The
    stream tracks it either way, because a stream that ends while a message is
    still waiting is an incomplete operation, not a successful one.
    """

    def emit(record_type: str, **fields: Any) -> None:
        if not silent:
            ndjson(record_type, **fields)

    cmd = _current_command()
    emit("start", command=cmd)
    buffer = ""
    terminal: dict[str, Any] | None = None
    accumulated_text: list[str] = []
    queue = queue_state if queue_state is not None else QueueObservation()

    try:
        for chunk in resp.iter_text():
            if raw_sse:
                emit("log", level="info", message=chunk)
                continue
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                parsed = parse_sse_frame(frame.strip())
                if parsed is None:
                    continue
                event_type = parsed["event"]
                payload = _payload_from_data(parsed["data"])
                data = event_data(payload)
                if event_type == "message.delta":
                    text = data.get("text") or data.get("delta") or ""
                    if text:
                        accumulated_text.append(str(text))
                        emit("text", text=text)
                    continue
                if event_type == "done":
                    if result_builder is not None:
                        terminal = ok(result_builder(data, "".join(accumulated_text)))
                    else:
                        terminal = ok({"stream": data, "text": "".join(accumulated_text)})
                    emit("result", **terminal)
                    return terminal
                if event_type == "error":
                    code = data.get("code") or "STREAM_ERROR"
                    message = data.get("message")
                    terminal = err(
                        code,
                        # Never stringify the dict into the message; carry it as structured `data`.
                        message or "Stream returned an error event.",
                        data.get("fix") or _error_fix(data),
                        data=data or None,
                    )
                    emit("error", **terminal)
                    return terminal
                if _observe_queue(event_type, data, queue):
                    emit(
                        "progress",
                        name="queue",
                        message=_queue_progress_message(queue),
                        **queue.as_result(),
                    )
                    continue
                ndjson_type, fields = map_public_event(event_type, data)
                emit(ndjson_type, **fields)
    except httpx.HTTPError as exc:
        terminal = err(
            "STREAM_ERROR",
            str(exc),
            "Check network connectivity and retry the stream command.",
        )
        emit("error", **terminal)
        return terminal

    if terminal is None:
        if queue.waiting:
            # The connection ended while the message was still waiting its turn.
            # Heartbeats and a queued status are not an answer: reporting ok here
            # would tell an agent its follow-up had been answered when the server
            # has not started it yet. The work itself is durable — point at it.
            terminal = err(
                "QUEUE_INCOMPLETE",
                f"The stream ended while queued message {queue.queued_message_id} was still "
                f"{'held' if queue.held else 'waiting'}; it has not produced a reply yet.",
                "The queued message is still durable server-side. Inspect it with "
                "`sumcli chats queue-show --chat <chat-id> --queued-message "
                f"{queue.queued_message_id}`"
                + (", resume the queue with `sumcli chats queue-resume`" if queue.held else "")
                + ", or withdraw it with `sumcli chats queue-withdraw`.",
                data={"queuedMessageId": queue.queued_message_id, **_queue_extra(queue)},
            )
            emit("error", **terminal)
            return terminal
        if result_builder is not None:
            terminal = ok(result_builder({}, "".join(accumulated_text)))
        else:
            terminal = ok({"text": "".join(accumulated_text)})
        emit("result", **terminal)
    return terminal


def _observe_queue(event_type: str, data: dict[str, Any], queue: QueueObservation) -> bool:
    """Record a durable-queue event; return True when it was one.

    ``status`` carries the receipt on acceptance (``queued`` or, after a Stop,
    ``held``); ``queue.updated`` reports FIFO progress while waiting. Both are
    consumed here rather than mapped to a generic log line so ``--follow`` shows
    the item id and its standing, which is the only handle a caller has before an
    assistant message exists.
    """
    if event_type not in ("status", "queue.updated"):
        return False
    queued_message_id = data.get("queuedMessageId") or data.get("queued_message_id")
    if not queued_message_id:
        return False
    queue.queued_message_id = str(queued_message_id)
    position = data.get("position")
    if isinstance(position, int):
        queue.position = position
    if event_type == "status":
        queue.held = data.get("message") == "held"
    return True


def _queue_progress_message(queue: QueueObservation) -> str:
    state = "held" if queue.held else "queued"
    if queue.position:
        return f"{state} ({queue.queued_message_id}, position {queue.position})"
    return f"{state} ({queue.queued_message_id})"


def _queue_extra(queue: QueueObservation) -> dict[str, Any]:
    extra: dict[str, Any] = {"held": queue.held}
    if queue.position is not None:
        extra["position"] = queue.position
    return extra


def _error_fix(data: dict[str, Any]) -> str:
    """Recovery hint for a stream error, keyed on the queue codes sum-api emits."""
    queued_message_id = data.get("queuedMessageId")
    if queued_message_id:
        return (
            "The queued message is still durable server-side. Inspect it with "
            f"`sumcli chats queue-show --chat <chat-id> --queued-message {queued_message_id}`."
        )
    return "Inspect error.data and retry."


def exit_if_stream_failed(terminal: dict[str, Any]) -> None:
    """Exit non-zero after NDJSON error terminal (stdout already has the envelope)."""
    if terminal.get("ok") is False:
        raise SystemExit(1)
