"""Guard that the docs' destructive-command lists match the code.

The same list is repeated in three files. It has drifted three times: `catalog
detach`, then `connections app-delete`, then `filesystem delete` — each caught by
a human reading one copy and not the others. Derive the truth from the source so
a new gate cannot be added without the docs failing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DOCS = ("README.md", "README-pypi.md", ".agents/skills/sumcli/SKILL.md")

# Commands whose refusal is hand-rolled rather than a require_confirm() call, so
# the scan below cannot see them. Both are verified by their own tests elsewhere.
_CUSTOM_GATES = frozenset({"schedules run", "config delete-profile"})


def _gated_commands() -> set[str]:
    """Every command that refuses to run without --confirm, read from the source."""
    found = set()
    for path in (_ROOT / "sum_cli").rglob("*.py"):
        found.update(re.findall(r'require_confirm\([^)]*action_name="([^"]+)"', path.read_text()))
    # The overwrite-only gate is conditional, so the docs describe it separately.
    found.discard("filesystem upload overwrite")
    return found | _CUSTOM_GATES


def _documented_commands(doc: str) -> set[str]:
    text = (_ROOT / doc).read_text()
    line = next(
        (ln for ln in text.split("\n") if "--confirm" in ln and "estructive" in ln),
        None,
    )
    assert line is not None, f"{doc} has no destructive-commands line"
    return set(re.findall(r"`([a-z]+(?: [a-z-]+)+)`", line))


@pytest.mark.parametrize("doc", _DOCS)
def test_destructive_list_covers_every_gated_command(doc: str) -> None:
    missing = _gated_commands() - _documented_commands(doc)
    assert not missing, f"{doc} omits confirm-gated commands: {sorted(missing)}"


@pytest.mark.parametrize("doc", _DOCS)
def test_destructive_list_has_no_phantom_commands(doc: str) -> None:
    """A command removed from the code must not linger in the docs."""
    listed = _documented_commands(doc)
    # The line also names `--confirm` itself and the conditional upload gate.
    phantom = {c for c in listed - _gated_commands() if c != "filesystem upload"}
    assert not phantom, f"{doc} lists commands that are not confirm-gated: {sorted(phantom)}"
