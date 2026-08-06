"""JSON envelopes and NDJSON streaming for sumcli (agent-first output).

Schema: https://joelclaw.com/cli-design-for-ai-agents

Output is agent-first by default: every command builds an envelope and emits it as
a single JSON line (or NDJSON for streams). A human-readable plain-text renderer is
layered on top via the module-level output mode — see ``set_output_mode`` and
``render_human``. Resolution order: explicit ``--output`` flag / ``SUMCLI_OUTPUT``
env, else auto-detect (human when stdout is a TTY, JSON when piped). Because every
call site funnels through ``emit``/``emit_error``/``ndjson``, the human mode lives
here and command bodies never change.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, NoReturn

import click

# Default page size for list-style commands when --count is omitted.
DEFAULT_LIST_COUNT = 50

OutputMode = Literal["json", "human"]


class OutputChoice(str, Enum):
    """Valid values for the --output flag.

    Typer maps a ``str, Enum``-typed option to a Click ``Choice``, so an invalid
    ``--output josn`` is rejected at parse time with a helpful error instead of
    silently falling through to the env/TTY default. The ``SUMCLI_OUTPUT`` env var
    stays lenient (resolve_output_mode ignores unknown values there) because a typo
    in a non-TTY context still safely resolves to JSON.
    """

    json = "json"
    human = "human"


# Active output mode. Resolved once by the root callback; defaults to JSON so that
# any code path that emits before resolution (or in tests) keeps the agent contract.
_output_mode: OutputMode = "json"

# Click/Typer context names that are not part of the user-facing command path.
_SKIP_COMMAND_PARTS = frozenset({"main", "sumcli", "root"})

# Root options that take a value, so the following token is that value (not a
# subcommand). Used by the argv fallback to skip past leading global options.
_ROOT_VALUE_OPTIONS = frozenset({"--output", "--profile", "--base-url"})


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_from_context() -> list[str]:
    ctx = click.get_current_context(silent=True)
    parts: list[str] = []
    c: click.Context | None = ctx
    while c is not None:
        if c.info_name and c.info_name not in _SKIP_COMMAND_PARTS:
            parts.append(c.info_name)
        c = c.parent
    return list(reversed(parts))


def _command_from_argv() -> list[str]:
    """Recover the subcommand path from argv, skipping leading global options.

    Fallback for when no live click context is available (e.g. get_current_context()
    returns None inside command bodies on click 8.4+). Skips root options and their
    values so `--output json config use` still resolves to `config use`; stops at the
    first non-root option, since per-subcommand parsing is click's job.
    """
    parts: list[str] = []
    argv = sys.argv
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-"):
            base = tok.split("=", 1)[0]
            if base not in _ROOT_VALUE_OPTIONS and tok not in ("-v", "--verbose"):
                break
            # Skip the option, plus its value when given as a separate token.
            i += 2 if base in _ROOT_VALUE_OPTIONS and "=" not in tok else 1
            continue
        parts.append(tok)
        i += 1
    return parts


def _current_command() -> str:
    parts = _command_from_context() or _command_from_argv()
    if not parts:
        return "sumcli"
    return "sumcli " + " ".join(parts)


def ok(result: dict[str, Any], next_actions: list[dict] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "command": _current_command(),
        "result": result,
        "next_actions": next_actions or [],
    }


def err(
    code: str,
    message: str,
    fix: str,
    next_actions: list[dict] | None = None,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "ok": False,
        "command": _current_command(),
        "error": error,
        "fix": fix,
        "next_actions": next_actions or [],
    }


def action(description: str, command: str, params: dict[str, dict] | None = None) -> dict[str, Any]:
    return {"description": description, "command": command, "params": params or {}}


def param(
    description: str,
    value: Any = None,
    default: Any = None,
    enum: list | None = None,
) -> dict[str, Any]:
    return {"value": value, "default": default, "enum": enum, "description": description}


def resolve_output_mode(explicit: str | None) -> OutputMode:
    """Pick the output mode from flag → env → TTY auto-detect.

    `explicit` is the value of the --output option (None when unset). Falls back to
    the SUMCLI_OUTPUT env var, then to "human" when stdout is an interactive TTY and
    "json" otherwise (so piped/redirected output stays machine-readable for agents).
    Unknown values are ignored in favor of the next source.
    """
    for candidate in (explicit, os.environ.get("SUMCLI_OUTPUT")):
        if not candidate:
            continue
        low = candidate.lower()
        if low == "json":
            return "json"
        if low == "human":
            return "human"
    return "human" if sys.stdout.isatty() else "json"


def get_output_mode() -> OutputMode:
    return _output_mode


def set_output_mode(mode: OutputMode) -> None:
    global _output_mode
    _output_mode = mode
    if mode == "human":
        # Human output uses non-ASCII glyphs (— …). On an ASCII/latin-1 stdout
        # (e.g. PYTHONIOENCODING=ascii, a C locale) those would raise
        # UnicodeEncodeError and abort the command. Degrade gracefully instead.
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                try:
                    reconfigure(errors="replace")
                except (ValueError, OSError):
                    pass


def emit(envelope: dict[str, Any]) -> None:
    if _output_mode == "human":
        print(render_human(envelope))
    else:
        print(json.dumps(envelope))


def emit_error(envelope: dict[str, Any]) -> NoReturn:
    if _output_mode == "human":
        print(render_human(envelope))
    else:
        print(json.dumps(envelope))
    sys.exit(1)


def invalid_request(message: str, fix: str) -> NoReturn:
    """Report client-side input validation failure and exit.

    NoReturn, not None: emit_error exits, so every call site ends the command.
    Annotating it lets a type checker flag a real fall-through instead of leaving
    the next reader to go check emit_error.
    """
    emit_error(err("INVALID_REQUEST", message, fix))


def ndjson(type_: str, **kwargs: Any) -> None:
    if _output_mode == "human":
        line = render_human_stream(type_, kwargs)
        if line is not None:
            print(line, flush=True)
        return
    print(json.dumps({"type": type_, "ts": _ts(), **kwargs}), flush=True)


# ── Human-readable rendering ──────────────────────────────────────────────────
#
# Plain text, no dependencies. The goal is scannable output for a person at a
# terminal, not a faithful serialization — agents should use the JSON mode.

_INDENT = "  "


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _fmt_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _humanize_key(key: str) -> str:
    return key.replace("_", " ")


# Cap a table at this many columns and this cell width so wide records stay scannable.
_MAX_TABLE_COLUMNS = 6
_MAX_CELL_WIDTH = 40
# Floor a shrunk column never drops below (so a squeezed table stays readable).
_MIN_CELL_WIDTH = 6
# Column names worth surfacing first when a record has more scalar fields than fit.
_PRIORITY_KEYS = ("id", "name", "title", "status", "type", "kind", "role")


def _terminal_width(default: int = 100) -> int:
    import shutil

    try:
        width = shutil.get_terminal_size((default, 24)).columns
    except (ValueError, OSError):
        return default
    return width if width and width > 0 else default


def _fit_widths(widths: list[int], indent: str, sep: str, budget: int) -> list[int]:
    """Shrink the widest columns until the table fits `budget` characters.

    Proportional-ish: repeatedly trim the currently-widest column by one until the
    total line width fits or every column has hit _MIN_CELL_WIDTH. Columns already
    at or below the floor are left alone.
    """
    overhead = len(indent) + len(sep) * (len(widths) - 1)

    def total() -> int:
        return overhead + sum(widths)

    while total() > budget:
        widest = max(range(len(widths)), key=lambda i: widths[i])
        if widths[widest] <= _MIN_CELL_WIDTH:
            break  # nothing left to safely trim
        widths[widest] -= 1
    return widths


def _pick_columns(rows: list[dict]) -> tuple[list[str], int]:
    """Choose up to _MAX_TABLE_COLUMNS scalar columns, identifier-like ones first.

    Returns (columns, dropped) where `dropped` counts *every* field omitted from the
    table — both scalar columns beyond the cap and non-scalar fields (nested dicts /
    lists) that a flat table can't show. Counting the nested fields matters: without
    it a row like {"id", "name", "members": [...]} renders as a clean two-column
    table with no hint that `members` was dropped — a silent loss vs. the JSON mode.
    """
    scalar_cols: list[str] = []
    all_keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in all_keys:
                all_keys.append(key)
            if key not in scalar_cols and _is_scalar(row[key]):
                scalar_cols.append(key)

    def rank(col: str) -> tuple[int, int]:
        low = col.lower()
        for i, p in enumerate(_PRIORITY_KEYS):
            if low == p or low.endswith(p):
                return (0, i)
        return (1, scalar_cols.index(col))

    # Identifier-like columns first (in _PRIORITY_KEYS order), then the rest in
    # original field order — `rank` is a stable key that encodes exactly that.
    chosen = sorted(scalar_cols, key=rank)[:_MAX_TABLE_COLUMNS]
    return chosen, len(all_keys) - len(chosen)


def _clip(text: str) -> str:
    """Cap a cell at _MAX_CELL_WIDTH regardless of terminal width.

    This bounds the *natural* column width before _fit_widths runs, so one giant
    value can't dominate the layout on a wide terminal (where _fit_widths wouldn't
    shrink it). _cell later re-clips to the final fitted width — the two passes use
    different bounds (absolute cap here, terminal-fit there) and are both intended.
    """
    return text if len(text) <= _MAX_CELL_WIDTH else text[: _MAX_CELL_WIDTH - 1] + "…"


def _max_columns_for_budget(indent: str, sep: str, budget: int) -> int:
    """How many floor-width columns fit `budget`, so the table never overflows.

    Each column needs at least _MIN_CELL_WIDTH plus a separator between columns.
    Always allow at least one column (a single wide column is clipped, not dropped).
    """
    fit = 1
    while len(indent) + len(sep) * fit + (fit + 1) * _MIN_CELL_WIDTH <= budget:
        fit += 1
    return max(1, fit)


def _render_table(rows: list[dict], lines: list[str], indent: str) -> bool:
    """Render a list of flat dicts as an aligned table. Returns False if not tabular."""
    if not rows or not all(isinstance(r, dict) for r in rows):
        return False
    columns, dropped = _pick_columns(rows)
    if not columns:
        return False
    sep = "  "
    budget = _terminal_width()
    # Drop the lowest-priority (trailing) columns that can't fit even at the floor,
    # so the rendered line never exceeds the terminal width. Fold them into `dropped`.
    fit = _max_columns_for_budget(indent, sep, budget)
    if fit < len(columns):
        dropped += len(columns) - fit
        columns = columns[:fit]
    headers = [_humanize_key(c) for c in columns]
    raw = [[_clip(_fmt_scalar(r.get(c))) for c in columns] for r in rows]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in raw)) if raw else len(headers[i])
        for i in range(len(columns))
    ]
    widths = _fit_widths(widths, indent, sep, budget)

    def _cell(text: str, w: int) -> str:
        text = text if len(text) <= w else (text[: w - 1] + "…" if w > 1 else text[:w])
        return text.ljust(w)

    lines.append(indent + sep.join(_cell(h, widths[i]) for i, h in enumerate(headers)))
    lines.append(indent + sep.join("-" * widths[i] for i in range(len(columns))))
    for row in raw:
        lines.append(indent + sep.join(_cell(c, widths[i]) for i, c in enumerate(row)))
    if dropped:
        note = (
            f"{indent}(+{dropped} more field{'s' if dropped != 1 else ''} per row; "
            "use --output json for all)"
        )
        # Keep the note within the budget too, so it can't wrap on a narrow terminal.
        lines.append(note if len(note) <= budget else note[: budget - 1] + "…")
    return True


def _render_value(value: Any, lines: list[str], indent: str, label: str | None = None) -> None:
    prefix = f"{indent}{_humanize_key(label)}: " if label else indent
    if _is_scalar(value):
        lines.append(f"{prefix}{_fmt_scalar(value)}")
        return
    if isinstance(value, list):
        if not value:
            lines.append(f"{prefix}(none)" if label else f"{indent}(none)")
            return
        if all(_is_scalar(v) for v in value):
            lines.append(f"{prefix}{', '.join(_fmt_scalar(v) for v in value)}")
            return
        if label:
            lines.append(f"{indent}{_humanize_key(label)}:")
        table_lines: list[str] = []
        if _render_table(value, table_lines, indent + _INDENT):
            lines.extend(table_lines)
            return
        for i, item in enumerate(value):
            lines.append(f"{indent + _INDENT}- [{i}]")
            _render_value(item, lines, indent + _INDENT * 2)
        return
    if isinstance(value, dict):
        if label:
            lines.append(f"{indent}{_humanize_key(label)}:")
            child_indent = indent + _INDENT
        else:
            child_indent = indent
        for key, sub in value.items():
            _render_value(sub, lines, child_indent, label=key)
        return
    lines.append(f"{prefix}{value}")


def _render_next_actions(actions: list[dict], lines: list[str]) -> None:
    usable = [a for a in actions if isinstance(a, dict) and a.get("command")]
    if not usable:
        return
    lines.append("")
    lines.append("Next:")
    for a in usable:
        desc = a.get("description")
        cmd = a.get("command")
        lines.append(f"{_INDENT}{desc}" if desc else _INDENT.rstrip())
        lines.append(f"{_INDENT}$ {cmd}")


def render_human(envelope: dict[str, Any]) -> str:
    """Render an ok()/err() envelope as scannable plain text."""
    lines: list[str] = []
    if envelope.get("ok") is False:
        error = envelope.get("error") or {}
        code = error.get("code", "ERROR")
        message = error.get("message", "")
        lines.append(f"Error [{code}]: {message}".rstrip())
        if error.get("data") is not None:
            _render_value(error["data"], lines, _INDENT, label="data")
        fix = envelope.get("fix")
        if fix:
            lines.append(f"Fix: {fix}")
    else:
        result = envelope.get("result")
        if isinstance(result, dict):
            if result:
                _render_value(result, lines, "")
            else:
                lines.append("OK")
        elif result is None:
            lines.append("OK")
        else:
            _render_value(result, lines, "")
    _render_next_actions(envelope.get("next_actions") or [], lines)
    if not lines:
        return "OK"
    return "\n".join(line.rstrip() for line in lines)


# Tracks whether the last streamed record was a mid-line token delta, so the next
# discrete record (step/log/result) starts on a fresh line instead of running on.
_stream_text_open = False


def render_human_stream(type_: str, fields: dict[str, Any]) -> str | None:
    """Render one streaming record as a human line. Returns None to suppress it."""
    global _stream_text_open
    if type_ == "start":
        # Reset per-stream state so a flag left set by a prior stream (in-process
        # reuse / tests) can't emit a stray leading newline into this one.
        _stream_text_open = False
        return None  # noise for a human; the output that follows is the signal
    if type_ == "text":
        # Token deltas — print inline (no newline per token) to read as flowing prose.
        sys.stdout.write(str(fields.get("text", "")))
        sys.stdout.flush()
        _stream_text_open = True
        return None
    # Any discrete record after streamed text needs a line break first.
    if _stream_text_open:
        sys.stdout.write("\n")
        _stream_text_open = False
    if type_ in ("result", "error"):
        # Terminal records normally carry a full envelope; render it (after any
        # streamed text). If the record is malformed (no `ok` key) we must still
        # surface it — an `error` that renders to nothing while the stream exits
        # non-zero is a silent failure. Fall back to a synthesized envelope.
        if fields.get("ok") is not None:
            return render_human(fields)
        if type_ == "error":
            message = str(fields.get("message") or fields.get("error") or fields)
            return render_human(err("STREAM_ERROR", message, "See above for details."))
        return render_human({"ok": True, "result": fields})
    if type_ == "step":
        return f"[{fields.get('status', '?')}] {fields.get('name', 'step')}"
    if type_ == "progress":
        msg = fields.get("message", "")
        return f"… {msg}" if msg else None
    if type_ == "log":
        level = fields.get("level", "info")
        return f"[{level}] {fields.get('message', '')}"
    return None


def truncate_list(
    items: list[Any],
    *,
    limit: int | None = None,
    count: int | None = None,
    default_limit: int = DEFAULT_LIST_COUNT,
) -> dict[str, Any]:
    cap = count if count is not None else (limit if limit is not None else default_limit)
    total = len(items)
    if total <= cap:
        return {"items": items, "showing": total, "total": total, "truncated": False}
    sliced = items[:cap]
    return {"items": sliced, "showing": cap, "total": total, "truncated": True}
