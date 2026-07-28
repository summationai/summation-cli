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
