"""Command tree discovery and OpenAPI-backed help tests."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from sum_cli.cli.main import app
from sum_cli.openapi_doc import (
    build_command_tree_envelope,
    build_resources,
    registered_typer_actions,
)

runner = CliRunner()

# Rich styles option-like tokens (e.g. --m2m) when FORCE_COLOR is set in CI.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _help_text(output: str) -> str:
    """Strip ANSI and collapse Rich/Click wrapping so multi-line blurbs compare equal."""
    cleaned = _ANSI_ESCAPE.sub("", output).replace("│", " ")
    return " ".join(cleaned.split())


def test_command_tree_envelope():
    env = build_command_tree_envelope()
    assert env["ok"] is True
    assert "resources" in env["result"]
    assert "projects" in env["result"]["resources"]
    assert "chats" in env["result"]["resources"]
    assert len(env["next_actions"]) >= 1


def test_command_tree_matches_typer_registration():
    """Discovery tree must stay in sync with registered Typer commands."""
    resources = build_resources()
    typer_actions = registered_typer_actions()
    assert set(resources.keys()) == set(typer_actions.keys())
    for resource, meta in resources.items():
        documented = set(meta["actions"].keys())
        assert documented == typer_actions[resource], (
            f"{resource}: tree {sorted(documented)} != typer {sorted(typer_actions[resource])}"
        )


def test_command_tree_action_blurbs_are_non_empty():
    resources = build_resources()
    for resource, meta in resources.items():
        for action, blurb in meta["actions"].items():
            assert blurb.strip(), f"{resource}.{action} has an empty blurb"


def test_typer_help_uses_openapi_action_blurbs():
    """Subcommand --help and group --help must match discovery blurbs."""
    resources = build_resources()
    for resource, meta in resources.items():
        group = runner.invoke(app, [resource, "--help"])
        assert group.exit_code == 0, group.stdout
        group_help = _help_text(group.stdout)
        assert _help_text(meta["description"]) in group_help, (
            f"{resource}: missing description in group help"
        )
        for action, blurb in meta["actions"].items():
            assert _help_text(blurb) in group_help, (
                f"{resource} {action}: missing blurb in group help"
            )
            cmd = runner.invoke(app, [resource, action, "--help"])
            assert cmd.exit_code == 0, cmd.stdout
            assert _help_text(blurb) in _help_text(cmd.stdout), (
                f"{resource} {action}: missing blurb in command help"
            )
