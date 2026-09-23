"""File upload encoding tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
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


class _FakeStream:
    """Minimal httpx.stream context manager yielding fixed bytes."""

    def __init__(
        self, status_code: int = 200, chunks: tuple[bytes, ...] = (b"raw ", b"bytes")
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.text = "error"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, _size: int = 0):
        yield from self._chunks

    def read(self):
        return b""


def _download_url_client(
    captured: dict, *, url: str = "https://s3.example/signed", file_name: str = "big.html"
):
    def capture_request(method: str, path: str, **kwargs: object):
        captured["method"] = method
        captured["path"] = path
        return {"data": {"url": url, "fileName": file_name, "expiresInSeconds": 900}}

    mock_client = MagicMock()
    mock_client.request.side_effect = capture_request
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    return mock_cm


def test_download_raw_streams_from_presigned_url(monkeypatch, tmp_path: Path) -> None:
    """Raw download mints a download URL and streams the bytes straight to disk (any size)."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    out = tmp_path / "o.bin"

    with (
        patch("sum_cli.resources.files.api_client", return_value=_download_url_client(captured)),
        patch("sum_cli.resources.files.httpx.stream", return_value=_FakeStream()) as stream,
    ):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-o", str(out)]
        )
    assert result.exit_code == 0
    # Raw goes through the download-url route, not the buffering content endpoint.
    assert captured["path"] == "/v1/projects/proj_1/files/file-1/download-url"
    # The presigned URL (not an API path) is what gets streamed.
    assert stream.call_args.args[1] == "https://s3.example/signed"
    assert out.read_bytes() == b"raw bytes"
    payload = json.loads(result.stdout)
    assert payload["result"]["bytes"] == 9


def test_download_raw_does_not_clobber_an_existing_part_sibling(
    monkeypatch, tmp_path: Path
) -> None:
    """A user's own report.pdf.part must survive a download to report.pdf (unique temp file)."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    out = tmp_path / "report.pdf"
    sibling = tmp_path / "report.pdf.part"
    sibling.write_bytes(b"user's own file")

    with (
        patch("sum_cli.resources.files.api_client", return_value=_download_url_client(captured)),
        patch("sum_cli.resources.files.httpx.stream", return_value=_FakeStream()),
    ):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-o", str(out)]
        )
    assert result.exit_code == 0
    assert out.read_bytes() == b"raw bytes"
    assert sibling.read_bytes() == b"user's own file"  # untouched


def test_download_raw_default_name_cannot_escape_the_working_directory(
    monkeypatch, tmp_path: Path
) -> None:
    """A file named with a traversal path must land as a basename in cwd, never outside it."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    client = _download_url_client(captured, file_name="../../.ssh/config")

    with (
        patch("sum_cli.resources.files.api_client", return_value=client),
        patch("sum_cli.resources.files.httpx.stream", return_value=_FakeStream()),
    ):
        result = runner.invoke(app, ["files", "download", "file-1", "--project", "proj_1"])
    assert result.exit_code == 0
    dest = Path(json.loads(result.stdout)["result"]["path"])
    assert dest.name == "config"  # traversal components stripped to a basename
    assert not (tmp_path / ".ssh").exists()  # nothing written outside the temp dir


def test_download_raw_leaves_no_partial_file_on_a_mid_stream_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """A dropped connection must not leave a truncated file at dest that reads as complete."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    out = tmp_path / "o.bin"

    class _FailingStream(_FakeStream):
        def iter_bytes(self, _size: int = 0):
            yield b"partial "
            raise httpx.ReadError("connection dropped")

    with (
        patch("sum_cli.resources.files.api_client", return_value=_download_url_client(captured)),
        patch("sum_cli.resources.files.httpx.stream", return_value=_FailingStream()),
    ):
        result = runner.invoke(
            app, ["files", "download", "file-1", "--project", "proj_1", "-o", str(out)]
        )
    assert result.exit_code == 1
    assert not out.exists()  # no truncated file at the destination
    assert not (tmp_path / "o.bin.part").exists()  # the partial is cleaned up too


def test_download_raw_without_output_writes_to_an_isolated_temp_dir(
    monkeypatch, tmp_path: Path
) -> None:
    """Without -o the file lands under its own name in a fresh temp dir — never in cwd, so it
    cannot clobber a same-named working-directory file."""
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("SUMMATION_PROJECT", "proj_1")
    captured: dict = {}
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.html").write_bytes(b"my own big.html")

    with (
        patch("sum_cli.resources.files.api_client", return_value=_download_url_client(captured)),
        patch("sum_cli.resources.files.httpx.stream", return_value=_FakeStream()),
    ):
        result = runner.invoke(app, ["files", "download", "file-1", "--project", "proj_1"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    dest = Path(payload["result"]["path"])
    assert dest.name == "big.html"
    assert dest.parent != tmp_path  # isolated temp dir, not the working directory
    assert dest.read_bytes() == b"raw bytes"
    assert (tmp_path / "big.html").read_bytes() == b"my own big.html"  # cwd file untouched


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
    mock_client.request.side_effect = ApiError(404, {"code": "not_found"})
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
