"""Shared --wait/--no-wait and --follow flags for long-running commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Callable

import httpx
import typer

from sum_cli.client import ApiError, Client
from sum_cli.output import emit, emit_error, err, ndjson
from sum_cli.streaming import QueueObservation, incomplete_terminal, stream_sse_response

WaitOption = Annotated[
    bool,
    typer.Option(
        "--wait/--no-wait",
        help="Wait until the operation completes.",
    ),
]
FollowOption = Annotated[
    bool,
    typer.Option(
        "--follow/--no-follow",
        help="Stream NDJSON progress to stdout (requires --wait).",
    ),
]
# Commands that stream by default (reports) use a tri-state so ``--no-wait`` alone stays
# valid: ``None`` means "not passed", and only an explicit ``--follow`` conflicts with
# ``--no-wait``. Resolve with ``resolve_follow`` before use.
OptionalFollowOption = Annotated[
    bool | None,
    typer.Option(
        "--follow/--no-follow",
        help="Stream NDJSON progress to stdout (requires --wait). Defaults on.",
    ),
]


def resolve_follow(*, wait: bool, follow: bool | None, default: bool) -> bool:
    """Apply a follow default, erroring only when ``--follow`` was passed with ``--no-wait``."""
    if follow is None:
        return default and wait
    validate_wait_follow(wait=wait, follow=follow)
    return follow


def validate_wait_follow(*, wait: bool, follow: bool) -> None:
    if follow and not wait:
        emit_error(
            err(
                "INVALID_FLAGS",
                "--follow requires --wait.",
                "Use --wait --follow, or --no-wait without --follow.",
            )
        )


@dataclass(frozen=True)
class StreamPostResult:
    streamed: bool
    body: Any = None


def post_with_wait_follow(
    client: Client,
    method: str,
    path: str,
    *,
    wait: bool,
    follow: bool,
    json: dict | None = None,
    result_builder: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    queue_state: QueueObservation | None = None,
    require_terminal: bool = False,
) -> StreamPostResult:
    """Consume the SSE stream the server always returns; emit live NDJSON only when --follow.

    The server responds with SSE for every wait/follow combination; the CLI varies only
    the surface output (live NDJSON vs. final envelope).

    `queue_state` is forwarded to the stream so a caller can report a durable queue
    receipt. Note that neither `--wait` nor `--no-wait` detaches: both drain the
    stream, so a queued send waits for its turn in-process (see README, "Long-running
    commands"). A true detach-on-accept mode would be a separate feature.
    """
    validate_wait_follow(wait=wait, follow=follow)
    queue = queue_state if queue_state is not None else QueueObservation()
    terminal: dict[str, Any] | None = None
    try:
        with client.stream(method, path, json=json) as resp:
            terminal = stream_sse_response(
                resp,
                result_builder=result_builder,
                silent=not (wait and follow),
                queue_state=queue,
                require_terminal=require_terminal,
            )
    except httpx.HTTPError as exc:
        # Entering the stream sends the request and waits for response headers, so
        # a failure here is outside stream_sse_response's own handler. sum-api
        # durably accepts the message *before* it starts streaming, so this window
        # is exactly where a send can land while the client sees only an error —
        # and the generic NETWORK_ERROR from main() would say "retry", which sends
        # it again. Callers with neither a key nor the always-terminal contract
        # (reports, grid) keep the generic handling.
        #
        # A failure raised while *closing* the stream arrives here too. If the
        # answer was already read, keep it: losing a completed reply to a
        # teardown error would be a worse outcome than the error itself.
        if terminal is None:
            failure = incomplete_terminal(
                queue, reason=str(exc), unconfirmed=True, require_terminal=require_terminal
            )
            if failure is None:
                raise
            # Under --follow the caller is reading NDJSON records, so the terminal
            # has to keep its `type` field like every other line.
            if wait and follow:
                ndjson("error", **failure)
            else:
                emit(failure)
            raise SystemExit(1) from exc
    except ApiError as exc:
        # sum-api forwarded the send but could not read the answer back: 504 on its
        # own 60s agent budget, 503 on a request error, 502 when agent-service's
        # acceptance was unreadable. The turn may already be durable, so the key —
        # not a retry — is the way back. main() renders it; this only attaches it.
        if queue.idempotency_key and exc.status >= 500:
            exc.idempotency_key = queue.idempotency_key
        raise
    # A terminal stream error is a failure of the whole operation: surface it as the
    # top-level envelope and exit 1, never let a caller nest it under ok:true.
    if terminal.get("ok") is False:
        if not (wait and follow):
            # --follow already emitted the error envelope as NDJSON; --wait has not.
            emit(terminal)
        raise SystemExit(1)
    if wait and follow:
        return StreamPostResult(streamed=True, body=terminal)
    return StreamPostResult(streamed=False, body=terminal.get("result") or terminal)
