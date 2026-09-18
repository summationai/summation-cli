"""Map sum-api public SSE streams to NDJSON lines."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from sum_cli.output import _current_command, err, ndjson, ok

# A `status` frame reporting one of these means the turn has not started.
_WAIT_STATUSES = frozenset({"queued", "held"})
# `done.data.status` values that mean the turn ended without a complete answer.
# agent-service emits complete/error/cancelled; the rest are defensive.
_FAILED_DONE_STATUSES = frozenset({"error", "cancelled", "canceled", "failed"})
# Events that only a turn actually running can produce.
_DISPATCH_EVENTS = frozenset(
    {"message.saved", "message.delta", "tool.started", "tool.input", "tool.completed"}
)


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
    # The key the send went out with. Carried here so a failure terminal can print
    # it: recovery means replaying the SAME key, and a caller that never saw the
    # generated one can only re-send with a fresh key — which enqueues a duplicate.
    idempotency_key: str | None = None
    # Set once the queued message's own turn starts producing events. After that
    # the message is no longer waiting, so an interrupted stream is a lost
    # connection to a running reply, not an undispatched backlog item.
    dispatched: bool = False
    bound_message_id: str | None = None
    # Set by a queued/held status even when the frame carries no id. Without it a
    # receipt-less status would degrade to "not queued" and the stream could report
    # success for a message that never ran.
    saw_wait: bool = False

    @property
    def waiting(self) -> bool:
        return self.queued_message_id is not None or self.saw_wait

    def as_result(self) -> dict[str, Any]:
        return {
            "queued_message_id": self.queued_message_id,
            "position": self.position,
            "held": self.held,
            "idempotency_key": self.idempotency_key,
        }

    def recovery_data(self) -> dict[str, Any]:
        """Structured recovery members for an error envelope (camelCase, as sum-api sends)."""
        data: dict[str, Any] = {"held": self.held}
        if self.queued_message_id:
            data["queuedMessageId"] = self.queued_message_id
        if self.idempotency_key:
            data["idempotencyKey"] = self.idempotency_key
        if self.bound_message_id:
            data["messageId"] = self.bound_message_id
        elif self.position is not None:
            data["position"] = self.position
        return data


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
    require_terminal: bool = False,
) -> dict[str, Any]:
    """Consume an SSE httpx stream and return a terminal envelope dict.

    When `silent` is False, also emit a live NDJSON record per event.

    `queue_state` is filled in when the reply was durably queued rather than
    dispatched immediately; pass one to report the receipt id to the caller. The
    stream tracks it either way, because a stream that ends while a message is
    still waiting is an incomplete operation, not a successful one.

    `require_terminal` says this endpoint always ends in its own `done`/`error`
    (the chats SSE routes do), so reaching EOF without one is a truncated stream
    rather than a quiet success. Endpoints whose streams may legitimately end
    without a terminal keep the permissive default.
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
                _observe_dispatch(event_type, data, queue)
                if event_type == "message.delta":
                    text = data.get("text") or data.get("delta") or ""
                    if text:
                        accumulated_text.append(str(text))
                        emit("text", text=text)
                    continue
                if event_type == "done":
                    text_so_far = "".join(accumulated_text)
                    if not (
                        data.get("messageId") or data.get("message_id") or data.get("status")
                    ):
                        # sum-api appends `done` with an EMPTY data object when the
                        # upstream stream ended without its own terminal
                        # (public_agent_sse_frames). Every genuine done carries both a
                        # message id and a status, so an identity-less one is a
                        # truncated reply wearing a success event's name.
                        incomplete = incomplete_terminal(
                            queue,
                            text=text_so_far,
                            reason="the server reported no result",
                            require_terminal=require_terminal,
                        )
                        if incomplete is not None:
                            emit("error", **incomplete)
                            return incomplete
                    done_status = str(data.get("status") or "complete")
                    if done_status in _FAILED_DONE_STATUSES:
                        # `done` is the terminal event, not a verdict. A turn that was
                        # cancelled (this CLI now ships `chats cancel`) or errored has
                        # no complete answer; reporting ok would hand a caller a
                        # truncated reply as if it were the whole one.
                        terminal = err(
                            f"REPLY_{done_status.upper()}",
                            data.get("errorMessage")
                            or f"The reply ended with status '{done_status}'.",
                            "The turn ended without a complete answer. error.data.text "
                            "holds the partial reply and error.data.messageId identifies the "
                            "message; re-read it with `sumcli chats events`, or run the "
                            "command again.",
                            data={k: v for k, v in {**data, "text": text_so_far}.items() if v},
                        )
                        emit("error", **terminal)
                        return terminal
                    if result_builder is not None:
                        terminal = ok(result_builder(data, text_so_far))
                    else:
                        terminal = ok({"stream": data, "text": text_so_far})
                    emit("result", **terminal)
                    return terminal
                if event_type == "error":
                    code = data.get("code") or "STREAM_ERROR"
                    message = data.get("message")
                    # sum-api projects an error frame to exactly {code, message}, so
                    # the receipt and the idempotency key can only come from what
                    # this stream observed. Without them the advice degrades to
                    # "retry", which re-sends an already-accepted turn.
                    error_data = {**queue.recovery_data(), **data}
                    if accumulated_text:
                        error_data.setdefault("text", "".join(accumulated_text))
                    terminal = err(
                        code,
                        # Never stringify the dict into the message; carry it as structured `data`.
                        message or "Stream returned an error event.",
                        data.get("fix") or _error_fix(error_data) + _replay_hint(queue),
                        data=error_data or None,
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
        # A dropped connection is not a dropped message: the server already accepted
        # it. The generic advice here is "retry the stream command", which for
        # `chats reply` mints a fresh idempotency key and asks the question twice.
        terminal = incomplete_terminal(
            queue, text="".join(accumulated_text), reason=str(exc)
        ) or err(
            "STREAM_ERROR",
            str(exc),
            "Check network connectivity and retry the stream command.",
        )
        emit("error", **terminal)
        return terminal

    if terminal is None:
        # Heartbeats and a queued status are not an answer: reporting ok here would
        # tell an agent its follow-up had been answered when the server may not have
        # started it. The work is durable either way — point at it.
        incomplete = incomplete_terminal(
            queue, text="".join(accumulated_text), require_terminal=require_terminal
        )
        if incomplete is not None:
            emit("error", **incomplete)
            return incomplete
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
    waiting_status = event_type == "status" and data.get("message") in _WAIT_STATUSES
    if not queued_message_id and not waiting_status:
        return False
    if queued_message_id:
        queue.queued_message_id = str(queued_message_id)
    queue.saw_wait = True
    position = data.get("position")
    if isinstance(position, int):
        queue.position = position
    if event_type == "status":
        queue.held = data.get("message") == "held"
    return True


def _observe_dispatch(event_type: str, data: dict[str, Any], queue: QueueObservation) -> None:
    """Note that a turn has started producing events, and which message it is.

    The message id is recorded even when nothing was queued: an interrupted plain
    reply can then be resumed by id instead of re-sent. ``dispatched`` stays a
    queue concept — it means *this queued item* left the backlog.
    """
    if event_type not in _DISPATCH_EVENTS:
        return
    if queue.waiting:
        queue.dispatched = True
    message_id = data.get("messageId") or data.get("message_id")
    if message_id:
        queue.bound_message_id = str(message_id)


def _replay_hint(queue: QueueObservation) -> str:
    """How to reconnect to an accepted send instead of sending it again.

    The idempotency key dedupes *any* reply, queued or not, so this applies from the
    moment the request goes out — not only once a queue receipt has arrived.
    """
    if not queue.idempotency_key:
        return " Do not re-send unless you know the message was not accepted."
    return (
        " Do not re-send with a new key — that asks the same question twice. Replay this "
        f"one with --idempotency-key {queue.idempotency_key} to reconnect to the same turn."
    )


def incomplete_terminal(
    queue: QueueObservation,
    *,
    text: str = "",
    reason: str = "",
    require_terminal: bool = False,
    unconfirmed: bool = False,
) -> dict[str, Any] | None:
    """Terminal for a send whose stream ended before its own done/error, or None.

    Three cases, most specific first. Dispatched: the reply is running (or stored)
    under a real message id, so withdrawal would 409 and the partial text is worth
    keeping. Still waiting: the backlog item is intact and withdrawable. Neither,
    but the send carried an idempotency key: the server accepted *something*, so the
    key is the way back and a plain retry would duplicate it. None means the caller
    keeps its existing behavior.

    ``unconfirmed`` is for a failure before any response was read, where the send
    may or may not have landed. The replay advice is identical — that is the point
    of the key — but the wording must not assert an acceptance we never saw.
    """
    suffix = f" ({reason})" if reason else ""
    data = queue.recovery_data()
    if not queue.dispatched and not queue.waiting:
        if not queue.idempotency_key and not require_terminal:
            return None
        if text:
            data["text"] = text
        resume_by_id = (
            " Re-read it with `sumcli chats events --chat <chat-id> --message "
            f"{queue.bound_message_id}`."
            if queue.bound_message_id
            else ""
        )
        return err(
            "STREAM_INCOMPLETE",
            (
                f"The connection failed before the reply could be read{suffix}."
                if unconfirmed
                else f"The stream ended before the reply finished{suffix}."
            ),
            (
                "The message may already have been accepted."
                if unconfirmed
                else "The message was accepted; the connection to it was lost."
            )
            + resume_by_id
            + _replay_hint(queue),
            data=data or None,
        )
    if queue.dispatched:
        target = queue.bound_message_id
        detail = f"message {target}" if target else "its bound message"
        if text:
            data["text"] = text
        label = queue.queued_message_id
        subject = f"queued message {label}" if label else "the queued message"
        return err(
            "QUEUE_STREAM_INCOMPLETE",
            f"The stream ended after {subject} started, before its reply finished{suffix}.",
            "The reply is still running or already stored server-side. Re-read "
            + (
                f"it with `sumcli chats events --chat <chat-id> --message {target}`."
                if target
                else (
                    f"{detail} with `sumcli chats queue-show --chat <chat-id> "
                    f"--queued-message {label}`, then `sumcli chats events`."
                    if label
                    else "it via `sumcli chats queue-list --chat <chat-id>`."
                )
            )
            + _replay_hint(queue),
            data=data,
        )
    state = "held" if queue.held else "waiting"
    resume = (
        " Release the hold with `sumcli chats queue-resume --chat <chat-id>`." if queue.held else ""
    )
    if text:
        data["text"] = text
    label = queue.queued_message_id
    subject = f"queued message {label}" if label else "the queued message"
    manage = (
        (
            f"Inspect it with `sumcli chats queue-show --chat <chat-id> --queued-message {label}`"
            f", or withdraw it with `sumcli chats queue-withdraw --chat <chat-id> "
            f"--queued-message {label} --confirm`."
        )
        if label
        else "List the backlog with `sumcli chats queue-list --chat <chat-id>`."
    )
    return err(
        "QUEUE_INCOMPLETE",
        f"The stream ended while {subject} was still {state}; it has not "
        f"produced a reply yet{suffix}.",
        "The queued message is still durable server-side. "
        + manage
        + resume
        + _replay_hint(queue),
        data=data,
    )


def _queue_progress_message(queue: QueueObservation) -> str:
    state = "held" if queue.held else "queued"
    if queue.position:
        return f"{state} ({queue.queued_message_id}, position {queue.position})"
    return f"{state} ({queue.queued_message_id})"


def _error_fix(data: dict[str, Any]) -> str:
    """Recovery hint for a stream error, keyed on the queue codes sum-api emits."""
    queued_message_id = data.get("queuedMessageId")
    if queued_message_id:
        return (
            "The queued message is still durable server-side. Inspect it with "
            f"`sumcli chats queue-show --chat <chat-id> --queued-message {queued_message_id}`. "
            "Do not re-send without the same idempotency key."
        )
    return "Inspect error.data."


def exit_if_stream_failed(terminal: dict[str, Any]) -> None:
    """Exit non-zero after NDJSON error terminal (stdout already has the envelope)."""
    if terminal.get("ok") is False:
        raise SystemExit(1)
