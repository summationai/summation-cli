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
_CUSTOM_GATES = frozenset(
    {"schedules run", "config delete-profile", "workflows activate", "workflows run"}
)

# One nesting level of parens, so a call like require_confirm(f(x), action_name="…")
# still matches. Plain [^)]* fails open at the first ")".
_REQUIRE_CONFIRM_ACTION = re.compile(
    r'require_confirm\((?:[^()]|\([^()]*\))*?action_name="([^"]+)"',
    re.DOTALL,
)


def _gated_commands() -> set[str]:
    """Every command that refuses to run without --confirm, read from the source."""
    found: list[str] = []
    calls = 0
    for path in (_ROOT / "sum_cli").rglob("*.py"):
        text = path.read_text()
        # Includes the `def require_confirm(` in commands.py (no action_name).
        calls += len(re.findall(r"\brequire_confirm\(", text))
        found.extend(_REQUIRE_CONFIRM_ACTION.findall(text))
    # calls counts the definition too; every real call site must yield an action_name
    # or a nested-paren miss would silently shrink the set and keep the docs tests green.
    assert len(found) == calls - 1, (
        f"require_confirm scan missed call sites: expected {calls - 1} action_name "
        f"matches, got {len(found)}"
    )
    # The overwrite-only gate is conditional, so the docs describe it separately.
    return (set(found) - {"filesystem upload overwrite"}) | _CUSTOM_GATES


def _documented_commands(doc: str) -> set[str]:
    text = (_ROOT / doc).read_text()
    line = next(
        (ln for ln in text.split("\n") if "--confirm" in ln and "estructive" in ln),
        None,
    )
    assert line is not None, f"{doc} has no destructive-commands line"
    return set(re.findall(r"`([a-z-]+(?: [a-z-]+)+)`", line))


@pytest.mark.parametrize("doc", _DOCS)
def test_destructive_list_covers_every_gated_command(doc: str) -> None:
    missing = _gated_commands() - _documented_commands(doc)
    assert not missing, f"{doc} omits confirm-gated commands: {sorted(missing)}"


@pytest.mark.parametrize("doc", _DOCS)
def test_destructive_list_has_no_phantom_commands(doc: str) -> None:
    """A command removed from the code must not linger in the docs."""
    listed = _documented_commands(doc)
    # The conditional upload gate is named on the line but excluded from the scan.
    phantom = {c for c in listed - _gated_commands() if c != "filesystem upload"}
    assert not phantom, f"{doc} lists commands that are not confirm-gated: {sorted(phantom)}"
