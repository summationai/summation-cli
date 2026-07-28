"""Happy-path smoke tests for production-critical sumcli resources."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.config_store import write_all
from sum_cli.cli.main import app

runner = CliRunner()


def _setup_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _write_config(tmp_path: Path, **profile_fields: str) -> Path:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            "default": {
                "base_url": "https://example.com",
                "access_token": "test-token",
                **profile_fields,
            }
        },
    )
    return cfg_file


def test_config_active_shows_default_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file = _write_config(tmp_path, default_project="proj_1")
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "active"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["default_project"] == "proj_1"


def test_config_set_project_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = _write_config(tmp_path)
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))

    result = runner.invoke(app, ["config", "set-project", "--project", "proj_1"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["result"]["default_project"] == "proj_1"


@pytest.mark.parametrize(
    ("invoke_args", "patch_module", "mock_return"),
    [
        pytest.param(
            ["auth", "whoami"],
            "sum_cli.resources.auth",
            {"data": {"tenant_id": "t1", "email": "user@example.com"}},
            id="auth-whoami",
        ),
        pytest.param(
            ["tenant", "show"],
            "sum_cli.resources.tenant",
            {"data": {"id": "org_1", "name": "Acme"}},
            id="tenant-show",
        ),
        pytest.param(
            ["files", "list", "--project", "proj_1"],
            "sum_cli.resources.files",
            {"data": {"entries": []}},
            id="files-list",
        ),
        pytest.param(
            ["chats", "list", "--project", "proj_1"],
            "sum_cli.resources.chats",
            {"data": {"chats": []}},
            id="chats-list",
        ),
        pytest.param(
            ["playbooks", "list", "--project", "proj_1"],
            "sum_cli.resources.playbooks",
            {"data": []},
            id="playbooks-list",
        ),
        pytest.param(
            ["connections", "list"],
            "sum_cli.resources.connections",
            {"data": []},
            id="connections-list",
        ),
        pytest.param(
            ["tables", "list"],
            "sum_cli.resources.tables",
            {"data": []},
            id="tables-list",
        ),
    ],
)
def test_resource_smoke(
    monkeypatch: pytest.MonkeyPatch,
    invoke_args: list[str],
    patch_module: str,
    mock_return: dict,
) -> None:
    _setup_api_env(monkeypatch)
    mock_client = MagicMock()
    mock_client.request.return_value = mock_return
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch(f"{patch_module}.api_client", return_value=mock_cm):
        result = runner.invoke(app, invoke_args)

    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
