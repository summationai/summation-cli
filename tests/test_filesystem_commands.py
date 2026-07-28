"""Filesystem download streaming and upload overwrite guards."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.filesystem.base import FsEntry, FsListResult

runner = CliRunner()


class _MockFileSystem:
    provider = "sharepoint"

    def __init__(self) -> None:
        self.upload_calls: list[dict] = []
        self.list_results: list[FsListResult] = []
        self.download_chunks: list[bytes] = []

    def close(self) -> None:
        return None

    def roots(self) -> list:
        return []

    def list(self, *, root: str, path: str | None = None, limit: int) -> FsListResult:
        if self.list_results:
            return self.list_results.pop(0)
        return FsListResult(entries=[])

    def download(self, *, root: str, item: str) -> Iterator[bytes]:
        for chunk in self.download_chunks:
            yield chunk

    def upload(self, *, root: str, parent: str | None, name: str, data: bytes) -> FsEntry:
        self.upload_calls.append(
            {"root": root, "parent": parent, "name": name, "data": data},
        )
        return FsEntry(id="new-item", name=name, kind="file", size=len(data))

    def mkdir(self, *, root: str, parent: str | None, name: str) -> FsEntry:
        raise NotImplementedError

    def delete(self, *, root: str, item: str) -> None:
        raise NotImplementedError


@pytest.fixture
def mock_fs() -> _MockFileSystem:
    return _MockFileSystem()


def _invoke_download(
    mock_fs: _MockFileSystem, tmp_path: Path, *, output: Path | None = None
) -> object:
    mock_fs.download_chunks = [b"hello", b" ", b"world"]
    with patch("sum_cli.resources.filesystem.get_filesystem", return_value=mock_fs):
        with patch("sum_cli.resources.filesystem.resolve_root", return_value="drive-1"):
            args = [
                "filesystem",
                "download",
                "--provider",
                "sharepoint",
                "--item",
                "item-1",
            ]
            if output is not None:
                args.extend(["--output", str(output)])
            return runner.invoke(app, args)


def test_download_streams_chunks_to_output(mock_fs: _MockFileSystem, tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    result = _invoke_download(mock_fs, tmp_path, output=out)
    assert result.exit_code == 0, result.stdout
    assert out.read_bytes() == b"hello world"
    body = json.loads(result.stdout)
    assert body["result"]["bytes"] == 11
    assert body["result"]["path"] == str(out)


def test_download_streams_chunks_to_temp_file(mock_fs: _MockFileSystem, tmp_path: Path) -> None:
    result = _invoke_download(mock_fs, tmp_path)
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    dest = Path(body["result"]["path"])
    assert dest.exists()
    assert dest.read_bytes() == b"hello world"
    assert body["result"]["bytes"] == 11


def test_upload_rejects_existing_file_without_overwrite(
    mock_fs: _MockFileSystem, tmp_path: Path
) -> None:
    local = tmp_path / "report.csv"
    local.write_text("new,data\n", encoding="utf-8")
    mock_fs.list_results = [
        FsListResult(entries=[FsEntry(id="old-item", name="report.csv", kind="file")]),
    ]
    with patch("sum_cli.resources.filesystem.get_filesystem", return_value=mock_fs):
        with patch("sum_cli.resources.filesystem.resolve_root", return_value="drive-1"):
            with patch("sum_cli.resources.filesystem.resolve_path", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "filesystem",
                        "upload",
                        "--provider",
                        "sharepoint",
                        "--file",
                        str(local),
                    ],
                )
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "FILE_EXISTS"
    assert mock_fs.upload_calls == []


def test_upload_overwrite_requires_confirm(mock_fs: _MockFileSystem, tmp_path: Path) -> None:
    local = tmp_path / "report.csv"
    local.write_text("new,data\n", encoding="utf-8")
    mock_fs.list_results = [
        FsListResult(entries=[FsEntry(id="old-item", name="report.csv", kind="file")]),
    ]
    with patch("sum_cli.resources.filesystem.get_filesystem", return_value=mock_fs):
        with patch("sum_cli.resources.filesystem.resolve_root", return_value="drive-1"):
            with patch("sum_cli.resources.filesystem.resolve_path", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "filesystem",
                        "upload",
                        "--provider",
                        "sharepoint",
                        "--file",
                        str(local),
                        "--overwrite",
                    ],
                )
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "CONFIRM_REQUIRED"
    assert mock_fs.upload_calls == []


def test_upload_overwrite_with_confirm(mock_fs: _MockFileSystem, tmp_path: Path) -> None:
    local = tmp_path / "report.csv"
    local.write_text("new,data\n", encoding="utf-8")
    mock_fs.list_results = [
        FsListResult(entries=[FsEntry(id="old-item", name="report.csv", kind="file")]),
    ]
    with patch("sum_cli.resources.filesystem.get_filesystem", return_value=mock_fs):
        with patch("sum_cli.resources.filesystem.resolve_root", return_value="drive-1"):
            with patch("sum_cli.resources.filesystem.resolve_path", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "filesystem",
                        "upload",
                        "--provider",
                        "sharepoint",
                        "--file",
                        str(local),
                        "--overwrite",
                        "--confirm",
                    ],
                )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["result"]["overwritten"] is True
    assert body["result"]["replaced_item"] == "old-item"
    assert len(mock_fs.upload_calls) == 1


def test_upload_new_file_without_overwrite_flags(mock_fs: _MockFileSystem, tmp_path: Path) -> None:
    local = tmp_path / "fresh.csv"
    local.write_text("a,b\n", encoding="utf-8")
    with patch("sum_cli.resources.filesystem.get_filesystem", return_value=mock_fs):
        with patch("sum_cli.resources.filesystem.resolve_root", return_value="drive-1"):
            with patch("sum_cli.resources.filesystem.resolve_path", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "filesystem",
                        "upload",
                        "--provider",
                        "sharepoint",
                        "--file",
                        str(local),
                    ],
                )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert "overwritten" not in body["result"]
    assert len(mock_fs.upload_calls) == 1
