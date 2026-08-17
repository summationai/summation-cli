"""Resolve and validate the root ``--intent`` / ``SUMCLI_INTENT`` value.

Agents should use the human's words when possible so later events can join to a
goal, not a summary of the command. Humans at a TTY may omit it. The normalized
value is sent to sum-api as ``X-Summation-Intent``.
"""

from __future__ import annotations

import sys
from urllib.parse import quote

from sum_cli.output import action, emit_error, err, param

INTENT_ENV = "SUMCLI_INTENT"
INTENT_HEADER = "X-Summation-Intent"
INTENT_MAX_LENGTH = 500

# Discovery, upgrades, and --version do not record a user goal.
_INTENT_EXEMPT_SUBCOMMANDS = frozenset({None, "update"})
_HELP_FLAGS = frozenset({"-h", "--help"})


def normalize_intent(raw: str | None) -> str | None:
    """Collapse whitespace (including newlines) into a single header-safe line."""
    if raw is None:
        return None
    value = " ".join(raw.split())
    return value or None


def encode_intent_header(value: str) -> str:
    """ASCII-safe header value. Non-ASCII is percent-encoded; spaces stay spaces."""
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return quote(value, safe=" ")
    return value


def stdout_is_tty() -> bool:
    """Isolated so tests can patch TTY detection without fighting CliRunner."""
    return sys.stdout.isatty()


def wants_help(argv: list[str] | None = None, extra: list[str] | None = None) -> bool:
    tokens = list(argv if argv is not None else sys.argv[1:])
    if extra:
        tokens.extend(extra)
    return any(tok.split("=", 1)[0] in _HELP_FLAGS for tok in tokens)


def intent_required(
    *,
    subcommand: str | None,
    isatty: bool | None = None,
    extra_tokens: list[str] | None = None,
) -> bool:
    if subcommand in _INTENT_EXEMPT_SUBCOMMANDS:
        return False
    if wants_help(extra=extra_tokens):
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
            f"--intent is {length} characters; maximum is {INTENT_MAX_LENGTH}.",
            f"Use the first {INTENT_MAX_LENGTH} characters of the human's request.",
        )
    )


def resolve_intent(
    raw: str | None,
    *,
    subcommand: str | None,
    extra_tokens: list[str] | None = None,
) -> str | None:
    """Normalize, enforce length, and require a value for agent (non-TTY) commands."""
    intent = normalize_intent(raw)
    if intent is not None and len(intent) > INTENT_MAX_LENGTH:
        reject_intent_too_long(len(intent))
    if intent_required(subcommand=subcommand, extra_tokens=extra_tokens) and intent is None:
        reject_missing_intent()
    return intent
