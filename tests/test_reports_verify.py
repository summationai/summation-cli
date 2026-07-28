"""Reports verify command tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()


def test_reports_verify_sends_empty_json_body(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_resp = MagicMock()
    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_resp
    mock_stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_cm
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with (
        patch("sum_cli.resources.reports.api_client", return_value=mock_cm),
        patch(
            "sum_cli.stream_options.stream_sse_response",
            return_value={"ok": True, "result": {"verification": {}}},
        ),
    ):
        result = runner.invoke(app, ["reports", "verify", "file-abc"])

    assert result.exit_code == 0
    call_kwargs = mock_client.stream.call_args
    assert call_kwargs[1]["json"] == {}
