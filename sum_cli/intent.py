"""Resolve and validate the root ``--intent`` / ``SUMCLI_INTENT`` value.

Agents should use the human's words when possible so later events can join to a
goal, not a summary of the command. Humans at a TTY may omit it. The normalized
value is sent to sum-api as ``X-Summation-Intent``.

Two steps, deliberately split. ``resolve_intent`` normalizes in the root callback
and never refuses, because that callback also runs for discovery and ``--help``.
``enforce_intent`` backs ``commands.require_intent``, which only runs when a
command really talks to sum-api. Keeping enforcement there means the
exemptions follow from control flow instead of from inspecting argv — Click never
runs a command body for ``--help``, so help cannot be mistaken for a real call and
an option *value* of ``--help`` cannot be mistaken for a help request.
"""

from __future__ import annotations

import re
import sys
from urllib.parse import quote

from sum_cli.output import action, emit_error, err, get_output_mode, param

INTENT_ENV = "SUMCLI_INTENT"
INTENT_HEADER = "X-Summation-Intent"
# Bytes on the wire, measured after percent-encoding: the cap exists to bound
# what every request carries, and non-ASCII expands up to 4x once encoded.
INTENT_MAX_LENGTH = 500

# Discovery, upgrades, and --version do not record a user goal.
#
# `auth` and `config` are a product choice, not a transport fact: some of them
# (auth whoami, auth status) do call sum-api and could carry the header. They are
# exempt because they are how a session gets set up — an agent must be able to log
# in and pick a profile before it has a goal to state, and gating that made the
# documented bootstrap sequence unrunnable.
#
# `filesystem` is exempt for the transport reason: those commands talk to the
# external provider (SharePoint, S3) with that provider's own credentials and
# never reach sum-api, so there is no X-Summation-Intent header to send.
_INTENT_EXEMPT_SUBCOMMANDS = frozenset({None, "update", "auth", "config", "filesystem"})

# C0 and DEL. str.split() drops CR/LF (so request splitting is already
# impossible) but leaves NUL, ESC, and friends intact: NUL makes h11 reject the
# send as an opaque NETWORK_ERROR, and ESC injects terminal escapes into any log
# that renders the value. Mirrors the allowlist user_agent() applies in client.py.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_intent(raw: str | None) -> str | None:
    """Collapse whitespace and strip control characters into one header-safe line."""
    if raw is None:
        return None
    # argv and os.environ decode with surrogateescape, so a byte that is not valid
    # UTF-8 (a Latin-1 paste, a non-UTF-8 locale) arrives as a lone surrogate. Those
    # cannot be UTF-8 encoded, so quote() would raise UnicodeEncodeError deep in the
    # send path and surface as an INTERNAL_ERROR carrying a raw codec message.
    value = raw.encode("utf-8", "replace").decode("utf-8")
    value = " ".join(_CONTROL_CHARS.sub(" ", value).split())
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


def caller_is_agent() -> bool:
    """Whether this invocation is machine-driven.

    Reuses the already-resolved output mode rather than probing the TTY again.
    ``resolve_output_mode`` reads flag -> SUMCLI_OUTPUT -> isatty, so a caller that
    asks for JSON has declared itself a machine even on a PTY. Testing isatty alone
    missed exactly that case: agent harnesses on a pty (tmux, script, pexpect,
    docker -t, CI) that set SUMCLI_OUTPUT=json were exempted, so the sessions the
    header exists to attribute were the ones sending no header.
    """
    return get_output_mode() == "json"


def intent_required(*, subcommand: str | None, isatty: bool | None = None) -> bool:
    """Whether this command must carry an intent.

    Deliberately has no notion of ``--help``. Enforcement happens at the point a
    command actually talks to sum-api (see ``require_intent``), and Click never
    runs a command body for a ``--help`` invocation — so help exempts itself and
    no code has to guess which argv token is a flag and which is an option value.
    """
    if subcommand in _INTENT_EXEMPT_SUBCOMMANDS:
        return False
    if isatty is not None:
        return not isatty
    return caller_is_agent()


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


def enforce_intent(intent: str | None, *, subcommand: str | None) -> None:
    """Enforce the intent contract for a command that is about to call sum-api.

    The two rules have different scopes, and conflating them broke things both
    ways. *Requiring* an intent depends on the caller (agents must, humans at a
    TTY need not). *Capping* one applies to any value that will really be sent,
    a human's included, because the cap bounds the header.

    Exempt groups skip both. `auth` reaches sum-api through api_client, so
    checking length ahead of the exemption failed `auth whoami` over a value that
    command never sends, while `config list` beside it succeeded.
    """
    if subcommand in _INTENT_EXEMPT_SUBCOMMANDS:
        return
    if intent is None:
        if intent_required(subcommand=subcommand):
            reject_missing_intent()
        return
    encoded = intent_header_length(intent)
    if encoded > INTENT_MAX_LENGTH:
        reject_intent_too_long(encoded)
