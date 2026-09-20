"""`sumcli tenant ...` command and org-resolution tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.commands import resolved_org
from sum_cli.config import load
from sum_cli.config_store import write_all
from sum_cli.constants import ACTIVE_PROFILE_KEY, META_SECTION

runner = CliRunner()

_ORGS_ENVELOPE = {
    "data": {
        "orgs": [
            {"org_id": "org-1", "name": "Home", "cluster": "us1"},
            {"org_id": "org-2", "name": "Customer", "cluster": "us1"},
        ],
        "total": 2,
        "showing": 2,
        "truncated": False,
    }
}


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUM_API_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SUM_API_BASE_URL", "https://example.com")


def _patched_client(return_value: object) -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.request.return_value = return_value
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = None
    api_client = MagicMock(return_value=cm)
    return api_client, client


def test_tenant_list_surfaces_pagination_fields() -> None:
    api_client, client = _patched_client(_ORGS_ENVELOPE)
    with patch("sum_cli.resources.tenant.api_client", api_client):
        result = runner.invoke(app, ["tenant", "list"])

    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)["result"]
    assert [o["org_id"] for o in body["orgs"]] == ["org-1", "org-2"]
    assert (body["total"], body["showing"], body["truncated"]) == (2, 2, False)


def test_tenant_discovery_runs_as_home_identity() -> None:
    # Discovery must not carry the resolved-org override, or a stale override locks the user out.
    api_client, client = _patched_client(_ORGS_ENVELOPE)
    with patch("sum_cli.resources.tenant.api_client", api_client):
        runner.invoke(app, ["tenant", "list"])

    assert api_client.call_args.kwargs.get("include_resolved_org") is False
    # and the max page is requested so `tenant use` validation stays whole for operators.
    assert client.request.call_args.kwargs.get("params") == {"limit": 500}


def test_tenant_use_saves_a_targetable_org() -> None:
    api_client, _ = _patched_client(_ORGS_ENVELOPE)
    with (
        patch("sum_cli.resources.tenant.api_client", api_client),
        patch("sum_cli.resources.tenant.update_profile_field") as save,
    ):
        result = runner.invoke(app, ["tenant", "use", "org-2"])

    assert result.exit_code == 0, result.stdout
    assert save.call_args.kwargs == {"resolved_org": "org-2"}


def test_tenant_use_rejects_an_untargetable_org() -> None:
    api_client, _ = _patched_client(_ORGS_ENVELOPE)
    with (
        patch("sum_cli.resources.tenant.api_client", api_client),
        patch("sum_cli.resources.tenant.update_profile_field") as save,
    ):
        result = runner.invoke(app, ["tenant", "use", "org-nope"])

    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "ORG_NOT_TARGETABLE"
    save.assert_not_called()


def test_tenant_use_clear_removes_the_key_without_discovery() -> None:
    api_client, _ = _patched_client(_ORGS_ENVELOPE)
    with (
        patch("sum_cli.resources.tenant.api_client", api_client),
        patch("sum_cli.resources.tenant.update_profile_field") as save,
    ):
        result = runner.invoke(app, ["tenant", "use", "--clear"])

    assert result.exit_code == 0, result.stdout
    assert save.call_args.kwargs == {"resolved_org": None}
    api_client.assert_not_called()  # --clear needs no fleet lookup


def test_resolved_org_override_wins_over_profile_default() -> None:
    ctx = SimpleNamespace(obj=SimpleNamespace(resolved_org="org-cli"))
    cfg = SimpleNamespace(resolved_org="org-profile")
    assert resolved_org(ctx, cfg) == "org-cli"


def test_resolved_org_falls_back_to_profile_default() -> None:
    ctx = SimpleNamespace(obj=SimpleNamespace(resolved_org=None))
    cfg = SimpleNamespace(resolved_org="org-profile")
    assert resolved_org(ctx, cfg) == "org-profile"


def test_resolved_org_is_none_when_unset() -> None:
    ctx = SimpleNamespace(obj=SimpleNamespace(resolved_org=None))
    cfg = SimpleNamespace(resolved_org=None)
    assert resolved_org(ctx, cfg) is None


def test_load_reads_resolved_org_from_profile_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file = tmp_path / "config"
    write_all(
        cfg_file,
        {
            META_SECTION: {ACTIVE_PROFILE_KEY: "default"},
            "default": {"base_url": "https://example.com", "resolved_org": "org-persisted"},
        },
    )
    monkeypatch.setenv("SUMMATION_CONFIG_FILE", str(cfg_file))
    assert load().resolved_org == "org-persisted"
