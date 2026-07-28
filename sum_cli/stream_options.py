"""Shared --wait/--no-wait and --follow flags for long-running commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Callable

import typer

from sum_cli.client import Client
from sum_cli.output import emit, emit_error, err
from sum_cli.streaming import stream_sse_response

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
) -> StreamPostResult:
    """Consume the SSE stream the server always returns; emit live NDJSON only when --follow.

    The server responds with SSE for every wait/follow combination; the CLI varies only
    the surface output (live NDJSON vs. final envelope).
    """
    validate_wait_follow(wait=wait, follow=follow)
    with client.stream(method, path, json=json) as resp:
        terminal = stream_sse_response(
            resp,
            result_builder=result_builder,
            silent=not (wait and follow),
        )
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
