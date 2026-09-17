"""Stream terminal exit code tests."""

from __future__ import annotations

import pytest

from sum_cli.streaming import exit_if_stream_failed


def test_exit_if_stream_failed_ok() -> None:
    exit_if_stream_failed({"ok": True, "result": {}})


def test_exit_if_stream_failed_error() -> None:
    with pytest.raises(SystemExit) as exc:
        exit_if_stream_failed(
            {
                "ok": False,
                "error": {"code": "STREAM_ERROR", "message": "boom"},
                "fix": "retry",
            }
        )
    assert exc.value.code == 1


def test_queued_eof_terminal_exits_nonzero() -> None:
    """A stream that ends mid-wait must fail the command, not fall through as ok."""
    from sum_cli.streaming import stream_sse_response

    class _Resp:
        def iter_text(self):
            yield (
                "event: status\nid: 0\n"
                'data: {"type":"status","sequence":0,'
                '"data":{"message":"queued","queuedMessageId":"qt_1"}}\n\n'
            )

    terminal = stream_sse_response(_Resp(), silent=True)
    with pytest.raises(SystemExit) as exc:
        exit_if_stream_failed(terminal)
    assert exc.value.code == 1


def test_post_with_wait_follow_exits_nonzero_on_a_queued_eof() -> None:
    """The reply path must surface the same failure, including without --follow."""
    from unittest.mock import MagicMock

    from sum_cli.stream_options import post_with_wait_follow
    from sum_cli.streaming import QueueObservation

    resp = MagicMock()
    resp.iter_text.return_value = iter(
        [
            "event: status\nid: 0\n"
            'data: {"type":"status","sequence":0,'
            '"data":{"message":"queued","queuedMessageId":"qt_2","position":1}}\n\n'
        ]
    )
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = resp
    stream_cm.__exit__.return_value = None
    client = MagicMock()
    client.stream.return_value = stream_cm

    state = QueueObservation()
    with pytest.raises(SystemExit) as exc:
        post_with_wait_follow(
            client, "POST", "/v1/x", wait=True, follow=False, queue_state=state
        )
    assert exc.value.code == 1
    # The receipt is still reported, so a caller can resume the durable work.
    assert state.queued_message_id == "qt_2"
