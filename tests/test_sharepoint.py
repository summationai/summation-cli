"""SharePoint backend and registry tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.filesystem.registry import FileSystemError, UnknownProvider, get_filesystem
from sum_cli.filesystem.sharepoint import (
    SharePointFileSystem,
    _graph_id,
    write_sharepoint_config,
)

runner = CliRunner()


def test_graph_id_encodes_exclamation() -> None:
    assert _graph_id("b!SEMqjral") == "b%21SEMqjral"


def test_graph_id_leaves_hyphens() -> None:
    assert _graph_id("01D6N5MY-UEFV") == "01D6N5MY-UEFV"


def test_get_filesystem_unknown_provider() -> None:
    with pytest.raises(UnknownProvider) as exc:
        get_filesystem("s3")
    assert exc.value.code == "UNKNOWN_PROVIDER"


def test_from_env_config_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    write_sharepoint_config(
        {
            "tenant_id": "from-config",
            "client_id": "client-1",
            "client_secret": "secret-1",
            "site_url": "host:/sites/Site",
        }
    )
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "from-env")
    fs = SharePointFileSystem.from_env()
    assert fs._tenant_id == "from-config"


def test_roots_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = SharePointFileSystem(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        site="host:/sites/Site",
        http=MagicMock(),
    )
    fs._site_id = "site-1"
    monkeypatch.setattr(fs, "_bearer", lambda: "token")
    calls = {"n": 0}

    def _graph(method: str, path: str, **kwargs: object) -> MagicMock:
        calls["n"] += 1
        return MagicMock(
            json=lambda: {"value": [{"id": "drive-1", "name": "Documents"}]},
            status_code=200,
        )

    monkeypatch.setattr(fs, "_graph", _graph)
    first = fs.roots()
    second = fs.roots()
    assert calls["n"] == 1
    assert first == second
    assert first[0].id == "drive-1"


def test_list_truncated_when_graph_has_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = SharePointFileSystem(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        site="host:/sites/Site",
        http=MagicMock(),
    )
    fs._site_id = "site-1"
    monkeypatch.setattr(fs, "_bearer", lambda: "token")

    page = {
        "value": [{"id": "item-1", "name": "a.txt", "file": {}, "size": 1}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/drive-1/root/children?$skip=1",
    }
    monkeypatch.setattr(
        fs,
        "_graph",
        lambda method, path, **kwargs: MagicMock(json=lambda: page, status_code=200),
    )

    result = fs.list(root="drive-1", limit=1)
    assert len(result.entries) == 1
    assert result.entries[0].name == "a.txt"
    assert result.truncated is True


def test_delete_calls_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = SharePointFileSystem(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        site="host:/sites/Site",
        http=MagicMock(),
    )
    calls: list[tuple[str, str]] = []

    def _graph(method: str, path: str, **kwargs: object) -> MagicMock:
        calls.append((method, path))
        return MagicMock(status_code=204)

    monkeypatch.setattr(fs, "_graph", _graph)
    fs.delete(root="b!drive", item="item-1")
    assert calls == [("DELETE", "/drives/b%21drive/items/item-1")]


def _download_fs(monkeypatch: pytest.MonkeyPatch, handler) -> SharePointFileSystem:
    fs = SharePointFileSystem(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        site="host:/sites/Site",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(fs, "_bearer", lambda: "token")
    return fs


def test_download_follows_graph_content_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    # Graph /content 302s to a pre-authenticated download URL; download() must
    # follow it and stream the real bytes, not the empty redirect body.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/content"):
            # Auth header is only valid against Graph; httpx must drop it on the
            # cross-origin hop to the CDN (it does), so assert it is gone there.
            assert request.headers.get("authorization") == "Bearer token"
            return httpx.Response(302, headers={"Location": "https://cdn.example/preauth"})
        assert request.url.host == "cdn.example"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"file-bytes")

    fs = _download_fs(monkeypatch, handler)
    assert b"".join(fs.download(root="b!drive", item="item-1")) == b"file-bytes"


def test_download_raises_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    fs = _download_fs(monkeypatch, handler)
    with pytest.raises(FileSystemError) as exc:
        b"".join(fs.download(root="b!drive", item="missing"))
    assert exc.value.code == "GRAPH_ERROR"


def test_auth_failed_on_token_error(monkeypatch: pytest.MonkeyPatch) -> None:
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=401, text="unauthorized")
    fs = SharePointFileSystem(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        site="host:/sites/Site",
        http=http,
    )
    with pytest.raises(FileSystemError) as exc:
        fs._bearer()
    assert exc.value.code == "AUTH_FAILED"


def test_import_env_rejects_partial_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config"
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg))
    env = tmp_path / ".env"
    env.write_text(
        "SHAREPOINT_TENANT_ID=tenant-1\nSHAREPOINT_CLIENT_ID=client-1\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["filesystem", "import-env", str(env), "--provider", "sharepoint"],
    )
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert "Incomplete SharePoint credentials" in body["error"]["message"]


@pytest.mark.parametrize("command", ["import-env", "set-defaults"])
def test_unknown_provider_rejected(command: str, tmp_path: Path) -> None:
    extra = [str(tmp_path / ".env")] if command == "import-env" else ["--root", "r"]
    result = runner.invoke(app, ["filesystem", command, *extra, "--provider", "s3"])
    assert result.exit_code != 0
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "UNKNOWN_PROVIDER"
