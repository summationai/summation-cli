"""App connection command tests with mocked HTTP (`connections app-*`)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _mock_client(return_value: object = None) -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.request.return_value = return_value
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None
    return client, cm


def _run(args: list[str], return_value: object = None) -> tuple[object, MagicMock]:
    client, cm = _mock_client(return_value)
    with patch("sum_cli.resources.connections.api_client", return_value=cm):
        result = runner.invoke(app, args)
    return result, client


# --- app-catalog ------------------------------------------------------------


def test_app_catalog_lists_available_connectors() -> None:
    result, client = _run(
        ["connections", "app-catalog"],
        {
            "data": {
                "apps": [
                    {"key": "netsuite", "display_name": "NetSuite", "coming_soon": False},
                    {"key": "coupa", "display_name": "Coupa", "coming_soon": True},
                ]
            }
        },
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert [a["key"] for a in body["result"]["apps"]] == ["netsuite", "coupa"]
    assert client.request.call_args[0] == ("GET", "/v1/connections/app/catalog")


def test_app_catalog_truncates_with_count() -> None:
    result, _ = _run(
        ["connections", "app-catalog", "--count", "1"],
        {"data": {"apps": [{"key": "netsuite"}, {"key": "share_point"}]}},
    )
    assert result.exit_code == 0, result.stdout
    assert [a["key"] for a in json.loads(result.stdout)["result"]["apps"]] == ["netsuite"]


def test_app_catalog_handles_empty_payload() -> None:
    result, _ = _run(["connections", "app-catalog"], {"data": {}})
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["apps"] == []


# --- app-tools --------------------------------------------------------------


def test_app_tools_uses_app_key_and_echoes_it() -> None:
    result, client = _run(
        ["connections", "app-tools", "netsuite"],
        {"data": {"tools": [{"slug": "suiteql", "name": "Run SuiteQL"}]}},
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["tools"][0]["slug"] == "suiteql"
    assert body["app_key"] == "netsuite"
    assert client.request.call_args[0] == (
        "GET",
        "/v1/connections/app/catalog/netsuite/tools",
    )


def test_app_tools_passes_underscored_catalog_key_verbatim() -> None:
    """Catalog keys are not provider slugs (``share_point`` vs ``sharepoint``)."""
    _, client = _run(["connections", "app-tools", "share_point"], {"data": {"tools": []}})
    assert client.request.call_args[0][1] == "/v1/connections/app/catalog/share_point/tools"


# --- app-list ---------------------------------------------------------------


def test_app_list_returns_connections() -> None:
    result, client = _run(
        ["connections", "app-list"],
        {
            "data": {
                "connections": [
                    {"id": "ac_1", "provider": "composio", "enabled_for_chat": True},
                ],
                "total": 1,
            }
        },
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert body["connections"][0]["id"] == "ac_1"
    assert client.request.call_args[0] == ("GET", "/v1/connections/app")


def test_app_list_omits_filter_by_default() -> None:
    """Server defaults enabled_for_chat_only to false; do not pin it."""
    _, client = _run(["connections", "app-list"], {"data": {"connections": []}})
    assert client.request.call_args[1]["params"] == {}


def test_app_list_sends_chat_filter_when_set() -> None:
    _, client = _run(
        ["connections", "app-list", "--enabled-for-chat-only"],
        {"data": {"connections": []}},
    )
    assert client.request.call_args[1]["params"] == {"enabled_for_chat_only": True}


# --- app-show ---------------------------------------------------------------


def test_app_show_unwraps_data() -> None:
    result, client = _run(
        ["connections", "app-show", "ac_1"],
        {"data": {"id": "ac_1", "status": "ACTIVE"}},
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["connection"]["id"] == "ac_1"
    assert client.request.call_args[0] == ("GET", "/v1/connections/app/ac_1")


# --- app-enable-chat / app-disable-chat -------------------------------------


def test_app_enable_chat_patches_true() -> None:
    result, client = _run(
        ["connections", "app-enable-chat", "ac_1"],
        {"data": {"id": "ac_1", "enabled_for_chat": True}},
    )
    assert result.exit_code == 0, result.stdout
    args, kwargs = client.request.call_args
    assert args == ("PATCH", "/v1/connections/app/ac_1")
    assert kwargs["json"] == {"enabled_for_chat": True}
    assert json.loads(result.stdout)["result"]["enabled_for_chat"] is True


def test_app_disable_chat_patches_false() -> None:
    result, client = _run(
        ["connections", "app-disable-chat", "ac_1"],
        {"data": {"id": "ac_1", "enabled_for_chat": False}},
    )
    assert result.exit_code == 0, result.stdout
    args, kwargs = client.request.call_args
    assert args == ("PATCH", "/v1/connections/app/ac_1")
    assert kwargs["json"] == {"enabled_for_chat": False}
    assert json.loads(result.stdout)["result"]["enabled_for_chat"] is False


# --- app-disconnect ---------------------------------------------------------


def test_app_disconnect_posts_and_echoes_id() -> None:
    result, client = _run(["connections", "app-disconnect", "ac_1"], {"data": {}})
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["result"]["disconnected"] == "ac_1"
    assert client.request.call_args[0] == (
        "POST",
        "/v1/connections/app/ac_1/disconnect",
    )


def test_app_disconnect_sends_no_body() -> None:
    _, client = _run(["connections", "app-disconnect", "ac_1"], {"data": {}})
    assert "json" not in client.request.call_args[1]


# --- app-delete -------------------------------------------------------------


def test_app_delete_requires_confirm() -> None:
    result, client = _run(["connections", "app-delete", "ac_1"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "CONFIRM_REQUIRED"
    client.request.assert_not_called()


def test_app_delete_sends_confirm_query_param() -> None:
    result, client = _run(["connections", "app-delete", "ac_1", "--confirm"], {"data": {}})
    assert result.exit_code == 0, result.stdout
    args, kwargs = client.request.call_args
    assert args == ("DELETE", "/v1/connections/app/ac_1")
    assert kwargs["params"] == {"confirm": True}
    assert json.loads(result.stdout)["result"]["deleted"] == "ac_1"


# --- namespacing ------------------------------------------------------------


def test_app_commands_do_not_shadow_data_connection_verbs() -> None:
    """The bare verbs stay bound to data connections."""
    _, client = _run(["connections", "list"], {"data": {"connections": []}})
    assert client.request.call_args[0] == ("GET", "/v1/connections/data")
