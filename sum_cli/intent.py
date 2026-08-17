"""Resolve and validate the root ``--intent`` / ``SUMCLI_INTENT`` value.

Agents should use the human's words when possible so later events can join to a
goal, not a summary of the command. Humans at a TTY may omit it. The normalized
value is sent to sum-api as ``X-Summation-Intent``.

Two steps, deliberately split. ``resolve_intent`` normalizes in the root callback
and never refuses, because that callback also runs for discovery and ``--help``.
``require_intent`` enforces the contract from ``commands.get_intent``, which only
runs when a command really talks to sum-api. Keeping enforcement there means the
exemptions follow from control flow instead of from inspecting argv — Click never
runs a command body for ``--help``, so help cannot be mistaken for a real call and
an option *value* of ``--help`` cannot be mistaken for a help request.
"""

from __future__ import annotations

import re
import sys
from urllib.parse import quote

from sum_cli.output import action, emit_error, err, param

INTENT_ENV = "SUMCLI_INTENT"
INTENT_HEADER = "X-Summation-Intent"
# Bytes on the wire, measured after percent-encoding: the cap exists to bound
# what every request carries, and non-ASCII expands up to 4x once encoded.
INTENT_MAX_LENGTH = 500

# Discovery, upgrades, and --version do not record a user goal. `auth` is
# exempt because login bootstraps a session before any goal exists, and
# `config` only ever touches the local config file — neither sends a request
# for the header to ride on, so gating them blocks setup and buys nothing.
_INTENT_EXEMPT_SUBCOMMANDS = frozenset({None, "update", "auth", "config"})

# C0 and DEL. str.split() drops CR/LF (so request splitting is already
# impossible) but leaves NUL, ESC, and friends intact: NUL makes h11 reject the
# send as an opaque NETWORK_ERROR, and ESC injects terminal escapes into any log
# that renders the value. Mirrors the allowlist user_agent() applies in client.py.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_intent(raw: str | None) -> str | None:
    """Collapse whitespace and strip control characters into one header-safe line."""
    if raw is None:
        return None
    value = " ".join(_CONTROL_CHARS.sub(" ", raw).split())
    return value or None


def encode_intent_header(value: str) -> str:
    """ASCII-safe header value. Non-ASCII is percent-encoded; spaces stay spaces."""
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return quote(value, safe=" ")
    return value


def intent_header_length(value: str) -> int:
    """Length of what actually goes on the wire, not of the source string."""
    return len(encode_intent_header(value))


def stdout_is_tty() -> bool:
    """Isolated so tests can patch TTY detection without fighting CliRunner."""
    return sys.stdout.isatty()


def intent_required(*, subcommand: str | None, isatty: bool | None = None) -> bool:
    """Whether this command must carry an intent.

    Deliberately has no notion of ``--help``. Enforcement happens at the point a
    command actually talks to sum-api (see ``require_intent``), and Click never
    runs a command body for a ``--help`` invocation — so help exempts itself and
    no code has to guess which argv token is a flag and which is an option value.
    """
    if subcommand in _INTENT_EXEMPT_SUBCOMMANDS:
        return False
    if isatty is None:
        isatty = stdout_is_tty()
    return not isatty


def reject_missing_intent() -> None:
    emit_error(
        err(
            "INTENT_REQUIRED",
            "This command requires --intent (the human's request, in their words when possible).",
            'Re-run with --intent "<human\'s request>", or set SUMCLI_INTENT. '
            "Use the human's words when possible — not a summary of the command.",
            next_actions=[
                action(
                    "Retry with intent",
                    'sumcli --intent "<human\'s request>" <resource> <action>',
                    params={
                        "human's request": param(
                            "The human's request, using their words when possible"
                        )
                    },
                ),
            ],
        )
    )


def reject_intent_too_long(length: int) -> None:
    emit_error(
        err(
            "INTENT_TOO_LONG",
            f"--intent encodes to {length} bytes; maximum is {INTENT_MAX_LENGTH}.",
            f"Shorten the intent to about {INTENT_MAX_LENGTH} characters of the "
            "human's request. Non-ASCII text encodes to more than one byte per character.",
        )
    )


def resolve_intent(raw: str | None) -> str | None:
    """Normalize the raw value. Never refuses — see ``require_intent``.

    Runs in the root callback, which also fires for exempt invocations such as
    discovery and ``--help``, so refusing here would lock an agent out of the very
    commands it needs to read the rules.
    """
    return normalize_intent(raw)


def require_intent(intent: str | None, *, subcommand: str | None) -> None:
    """Enforce the intent contract for a command that is about to call sum-api."""
    if intent is not None:
        encoded = intent_header_length(intent)
        if encoded > INTENT_MAX_LENGTH:
            reject_intent_too_long(encoded)
    if intent is None and intent_required(subcommand=subcommand):
        reject_missing_intent()
