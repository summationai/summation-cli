"""Map sum-api public SSE streams to NDJSON lines."""

from __future__ import annotations

import json
from typing import Any

import httpx

from sum_cli.output import _current_command, err, ndjson, ok


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
) -> dict[str, Any]:
    """Consume an SSE httpx stream and return a terminal envelope dict.

    When `silent` is False, also emit a live NDJSON record per event.
    """

    def emit(record_type: str, **fields: Any) -> None:
        if not silent:
            ndjson(record_type, **fields)

    cmd = _current_command()
    emit("start", command=cmd)
    buffer = ""
    terminal: dict[str, Any] | None = None
    accumulated_text: list[str] = []

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
                if event_type == "message.delta":
                    text = payload.get("text") or payload.get("delta") or ""
                    if text:
                        accumulated_text.append(str(text))
                        emit("text", text=text)
                    continue
                if event_type == "done":
                    if result_builder is not None:
                        terminal = ok(result_builder(payload, "".join(accumulated_text)))
                    else:
                        terminal = ok({"stream": payload, "text": "".join(accumulated_text)})
                    emit("result", **terminal)
                    return terminal
                if event_type == "error":
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
                    code = payload.get("code") or (data or {}).get("code") or "STREAM_ERROR"
                    message = payload.get("message") or (data or {}).get("message")
                    terminal = err(
                        code,
                        # Never stringify the dict into the message; carry it as structured `data`.
                        message or "Stream returned an error event.",
                        payload.get("fix") or "Inspect error.data and retry.",
                        data=data if data is not None else (payload or None),
                    )
                    emit("error", **terminal)
                    return terminal
                ndjson_type, fields = map_public_event(event_type, payload)
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
        if result_builder is not None:
            terminal = ok(result_builder({}, "".join(accumulated_text)))
        else:
            terminal = ok({"text": "".join(accumulated_text)})
        emit("result", **terminal)
    return terminal


def exit_if_stream_failed(terminal: dict[str, Any]) -> None:
    """Exit non-zero after NDJSON error terminal (stdout already has the envelope)."""
    if terminal.get("ok") is False:
        raise SystemExit(1)
