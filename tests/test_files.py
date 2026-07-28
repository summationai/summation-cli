"""File upload encoding tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.client import ApiError
from sum_cli.file_content import is_binary_path, read_file_write_payload

runner = CliRunner()


def test_is_binary_path_pdf(tmp_path: Path) -> None:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert is_binary_path(p)


def test_read_file_write_payload_binary(tmp_path: Path) -> None:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"\x00\x01\x02")
    payload = read_file_write_payload(p)
    assert payload["encoding"] == "base64"
    assert "content" in payload


def test_upload_sends_base64_for_binary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"\xff\xfe")

    captured: dict = {}

    def capture_request(method: str, path: str, **kwargs: object) -> dict:
        captured.update(kwargs.get("json", {}) if isinstance(kwargs.get("json"), dict) else {})
        return {"data": {"id": "f1"}}

    mock_client = MagicMock()
    mock_client.request.side_effect = capture_request
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.files.api_client", return_value=mock_cm):
        result = runner.invoke(app, ["files", "upload", str(pdf), "--project", "proj_1"])
    assert result.exit_code == 0
    assert captured.get("encoding") == "base64"


def _download_client(captured: dict) -> MagicMock:
    def capture_request_bytes(method: str, path: str, **kwargs: object) -> bytes:
        captured["method"] = method
        captured["path"] = path
        captured["params"] = kwargs.get("params")
        return b"%PDF-1.7 bytes"

    mock_client = MagicMock()
    mock_client.request_bytes.side_effect = capture_request_bytes
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_cm


def test_download_raw_hits_file_content(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    out = tmp_path / "o.bin"

    with patch("sum_cli.resources.files.api_client", return_value=_download_client(captured)):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-o", str(out)]
        )
    assert result.exit_code == 0
    assert captured["path"] == "/v1/projects/proj_1/files/file-1/content"
    assert captured["params"] is None


def test_download_pdf_hits_report_content_with_format(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    out = tmp_path / "o.pdf"

    with patch("sum_cli.resources.files.api_client", return_value=_download_client(captured)):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-f", "pdf", "-o", str(out)]
        )
    assert result.exit_code == 0
    assert captured["path"] == "/v1/projects/proj_1/reports/file-1/content"
    assert captured["params"] == {"format": "pdf"}


def test_download_rejects_unknown_format(monkeypatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}

    with patch("sum_cli.resources.files.api_client", return_value=_download_client(captured)):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-f", "pptx"]
        )
    assert result.exit_code == 1
    assert captured == {}  # rejected before any network call
    assert "pptx" in result.stdout


def test_download_raw_404_points_at_format(monkeypatch) -> None:
    """A raw 404 (e.g. an .sdoc has no raw bytes) must suggest --format, not auth."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_client = MagicMock()
    mock_client.request_bytes.side_effect = ApiError(404, {"code": "not_found"})
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.files.api_client", return_value=mock_cm):
        result = runner.invoke(app, ["files", "download", "file-1", "--project", "proj_1"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "--format" in payload["fix"]


def test_download_render_404_points_at_raw(monkeypatch) -> None:
    """A render 404 (e.g. a plain file or wrong id) must suggest --format raw, not auth."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")

    mock_client = MagicMock()
    mock_client.request_bytes.side_effect = ApiError(404, {"code": "not_found"})
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("sum_cli.resources.files.api_client", return_value=mock_cm):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-f", "pdf"]
        )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "--format raw" in payload["fix"]
