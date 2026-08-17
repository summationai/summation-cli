"""Root --intent / SUMCLI_INTENT and X-Summation-Intent header tests."""

from __future__ import annotations

import json
import sys
import time
from unittest.mock import MagicMock

from typer.testing import CliRunner

from sum_cli.auth import TokenResult
from sum_cli.cli.main import app
from sum_cli.client import Client
from sum_cli.config import Config
from sum_cli.intent import (
    INTENT_HEADER,
    INTENT_MAX_LENGTH,
    encode_intent_header,
    intent_required,
    normalize_intent,
)
from sum_cli.output import _command_from_argv

runner = CliRunner()


def test_normalize_intent_collapses_whitespace() -> None:
    assert normalize_intent("  convert   my\nweekly recap\t") == "convert my weekly recap"
    assert normalize_intent("   ") is None
    assert normalize_intent(None) is None


def test_encode_intent_header_keeps_ascii() -> None:
    assert encode_intent_header("convert my weekly recap") == "convert my weekly recap"


def test_encode_intent_header_percent_encodes_non_ascii() -> None:
    assert encode_intent_header("café") == "caf%C3%A9"


def test_intent_required_exempts_meta_and_tty() -> None:
    assert intent_required(subcommand=None, isatty=False) is False
    assert intent_required(subcommand="update", isatty=False) is False
    assert intent_required(subcommand="projects", isatty=True) is False
    assert intent_required(subcommand="projects", isatty=False) is True


def test_normalize_intent_strips_control_characters() -> None:
    # CR/LF were already collapsed by split(); NUL, ESC and DEL were not, and
    # reached the header verbatim (ESC on the wire, NUL as an opaque send error).
    assert normalize_intent("a\x00b") == "a b"
    assert normalize_intent("red\x1b[31mtext") == "red [31mtext"
    assert normalize_intent("a\x7fb") == "a b"
    assert normalize_intent("a\r\nX-Evil: 1") == "a X-Evil: 1"
    assert normalize_intent("\x00\x1b") is None


def test_missing_intent_is_required_when_piped(monkeypatch) -> None:
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    result = runner.invoke(app, ["projects", "list"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "INTENT_REQUIRED"


def test_tty_may_omit_intent(monkeypatch) -> None:
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: True)
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True


def test_intent_flag_satisfies_requirement(monkeypatch) -> None:
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    result = runner.invoke(app, ["--intent", "list my projects", "config", "list"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True


def test_intent_env_satisfies_requirement(monkeypatch) -> None:
    monkeypatch.setenv("SUMCLI_INTENT", "list my projects")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0


def test_whitespace_only_intent_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    result = runner.invoke(app, ["--intent", "   ", "projects", "list"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INTENT_REQUIRED"


def test_bootstrap_groups_do_not_require_intent(monkeypatch) -> None:
    """`auth` and `config` set up a session before there is a goal to state.

    A product choice, not a transport one: `auth whoami`/`auth status` do call
    sum-api. Gating them made the documented bootstrap sequence unrunnable.
    """
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["ok"] is True


def test_filesystem_does_not_require_intent(monkeypatch) -> None:
    """filesystem talks to the external provider, never to sum-api, so there is
    no X-Summation-Intent header for an intent to ride on.
    """
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    result = runner.invoke(app, ["filesystem", "roots", "--provider", "sharepoint"])
    assert "INTENT_REQUIRED" not in result.stdout


def test_intent_too_long(monkeypatch) -> None:
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: True)
    result = runner.invoke(app, ["--intent", "x" * (INTENT_MAX_LENGTH + 1), "projects", "list"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INTENT_TOO_LONG"


def test_intent_cap_counts_encoded_bytes(monkeypatch) -> None:
    """The cap bounds the wire value, so percent-encoding expansion counts.

    500 non-ASCII characters used to pass and ship a 3000-byte header.
    """
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: True)
    result = runner.invoke(app, ["--intent", "é" * INTENT_MAX_LENGTH, "projects", "list"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "INTENT_TOO_LONG"


def test_discovery_and_version_do_not_require_intent(monkeypatch) -> None:
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    tree = runner.invoke(app, [])
    assert tree.exit_code == 0
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0


def test_help_does_not_require_intent(monkeypatch) -> None:
    # No sys.argv patching: help exempts itself because Click never runs the
    # command body, so enforcement at the point of use is never reached.
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    for args in (["projects", "list", "--help"], ["projects", "--help"], ["--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, args
        assert "INTENT_REQUIRED" not in result.stdout


def test_help_valued_option_does_not_bypass_intent(monkeypatch) -> None:
    """An option *value* of --help/-h must not read as a help request.

    Scanning argv could not tell the two apart, so `--message "--help"` skipped
    the gate and called sum-api with no intent.
    """
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    for value in ("--help", "-h"):
        result = runner.invoke(app, ["chats", "create", "--message", value])
        assert result.exit_code == 1, value
        assert json.loads(result.stdout)["error"]["code"] == "INTENT_REQUIRED", value


def test_long_intent_does_not_break_exempt_commands(monkeypatch) -> None:
    """A session-wide over-long SUMCLI_INTENT must not lock an agent out.

    Discovery and --help are how an agent reads the rules; refusing them for a
    too-long value denies it the means to fix the error.
    """
    monkeypatch.setenv("SUMCLI_INTENT", "x" * (INTENT_MAX_LENGTH + 100))
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    assert runner.invoke(app, []).exit_code == 0
    assert runner.invoke(app, ["--version"]).exit_code == 0
    assert runner.invoke(app, ["projects", "list", "--help"]).exit_code == 0


def test_intent_after_subcommand_is_an_error(monkeypatch) -> None:
    result = runner.invoke(app, ["config", "list", "--intent", "oops"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_command_path_skips_intent_option(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["sumcli", "--intent", "list projects", "config", "list"])
    assert _command_from_argv() == ["config", "list"]


def _cfg() -> Config:
    return Config(
        base_url="https://example.com",
        access_token=None,
        device_login_credential=None,
        client_id="cid",
        client_secret="secret",
        m2m_scope=None,
        profile="default",
        default_project=None,
        source="test",
    )


def test_client_sends_intent_header(monkeypatch) -> None:
    Client.clear_token_cache()
    monkeypatch.setattr(
        "sum_cli.client.acquire_token",
        lambda _cfg, _http: TokenResult("tok", time.monotonic() + 10_000),
    )
    captured: dict = {}

    def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"{}"
        resp.json.return_value = {}
        return resp

    with Client(_cfg(), intent="convert my weekly recap") as client:
        client._http.request = fake_request
        client.request("GET", "/v1/projects")
    assert captured["headers"][INTENT_HEADER] == "convert my weekly recap"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    Client.clear_token_cache()


def test_cli_invocation_puts_intent_on_the_wire(monkeypatch) -> None:
    """End-to-end: --intent reaches the outgoing header through the real CLI path.

    The other header tests build a Client directly, so they cannot catch the
    get_intent(ctx) wiring in commands.py being dropped.
    """
    Client.clear_token_cache()
    monkeypatch.delenv("SUMCLI_INTENT", raising=False)
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    monkeypatch.setattr("sum_cli.intent.stdout_is_tty", lambda: False)
    monkeypatch.setattr(
        "sum_cli.client.acquire_token",
        lambda _cfg, _http: TokenResult("tok", time.monotonic() + 10_000),
    )
    captured: dict = {}

    def fake_request(self, method: str, url: str, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"{}"
        resp.json.return_value = {"data": []}
        return resp

    monkeypatch.setattr("httpx.Client.request", fake_request)
    result = runner.invoke(app, ["--intent", "convert my weekly recap", "projects", "list"])
    assert result.exit_code == 0, result.stdout
    assert captured["headers"][INTENT_HEADER] == "convert my weekly recap"
    Client.clear_token_cache()


def test_client_omits_intent_header_when_unset(monkeypatch) -> None:
    Client.clear_token_cache()
    monkeypatch.setattr(
        "sum_cli.client.acquire_token",
        lambda _cfg, _http: TokenResult("tok", time.monotonic() + 10_000),
    )
    captured: dict = {}

    def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"{}"
        resp.json.return_value = {}
        return resp

    with Client(_cfg()) as client:
        client._http.request = fake_request
        client.request("GET", "/v1/projects")
    assert INTENT_HEADER not in captured["headers"]
    Client.clear_token_cache()
