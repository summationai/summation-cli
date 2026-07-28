"""Output envelope contract tests."""

from __future__ import annotations

import io
import json

import pytest

from sum_cli.output import (
    action,
    err,
    ok,
    param,
    render_human,
    render_human_stream,
    resolve_output_mode,
    set_output_mode,
    truncate_list,
)


def test_ok_envelope_shape():
    env = ok({"x": 1}, next_actions=[action("Next", "sumcli projects list")])
    assert env["ok"] is True
    assert "command" in env
    assert env["result"] == {"x": 1}
    assert len(env["next_actions"]) == 1
    assert env["next_actions"][0]["command"] == "sumcli projects list"


def test_err_envelope_shape():
    env = err("CODE", "msg", "fix me")
    assert env["ok"] is False
    assert env["error"]["code"] == "CODE"
    assert env["fix"] == "fix me"


def test_truncate_list():
    listed = truncate_list(list(range(100)), count=10)
    assert listed["truncated"] is True
    assert listed["showing"] == 10
    assert listed["total"] == 100


def test_param_schema():
    p = param("Project ID", value="proj_1", default=None, enum=["a"])
    assert p["description"] == "Project ID"
    assert p["value"] == "proj_1"


def test_emit_is_json_serializable():
    env = ok({"n": 1})
    json.dumps(env)


# ── Human-readable rendering ──────────────────────────────────────────────────


def test_render_human_scalar_result():
    out = render_human(ok({"deleted": "p1"}))
    assert out == "deleted: p1"


def test_render_human_empty_result_is_ok():
    assert render_human(ok({})) == "OK"


def test_render_human_nested_and_none():
    out = render_human(ok({"project": {"id": "p1", "description": None, "tags": ["a", "b"]}}))
    assert "project:" in out
    assert "id: p1" in out
    assert "description: —" in out  # None renders as em dash
    assert "tags: a, b" in out  # scalar list collapses inline


def test_render_human_list_of_dicts_is_table():
    rows = [{"id": "p1", "name": "Alpha"}, {"id": "p2", "name": "Beta"}]
    out = render_human(ok({"projects": rows}))
    lines = out.splitlines()
    assert lines[0] == "projects:"
    # header + separator + two rows, aligned columns
    assert "id" in lines[1] and "name" in lines[1]
    assert set(lines[2].strip()) <= {"-", " "}
    assert "p1" in out and "Alpha" in out


def test_render_human_no_trailing_whitespace():
    rows = [{"id": "p1", "name": "Alpha"}, {"id": "p2", "name": "B"}]
    out = render_human(ok({"projects": rows}))
    assert all(line == line.rstrip() for line in out.splitlines())


def test_render_human_wide_table_caps_columns():
    rows = [{f"col{i}": f"v{i}{j}" for i in range(10)} for j in range(2)]
    out = render_human(ok({"items": rows}))
    header = out.splitlines()[1]
    # At most _MAX_TABLE_COLUMNS columns surface; the rest are summarized.
    assert header.count("col") <= 6
    assert "more field" in out


def test_render_human_table_prioritizes_identifier_columns():
    rows = [{"createdAt": "t", "blob": "x", "id": "p1", "name": "Alpha", "z": 1, "y": 2, "w": 3}]
    out = render_human(ok({"items": rows}))
    header = out.splitlines()[1]
    # id/name are promoted to the front despite their original position, and the
    # rest follow in original field order.
    cols = header.split()
    assert cols[0] == "id"
    assert cols[1] == "name"
    # non-priority fields keep their original relative order (createdAt before blob)
    assert cols.index("createdAt") < cols.index("blob")


def test_render_human_clips_long_cells():
    rows = [{"id": "p1", "desc": "x" * 100}]
    out = render_human(ok({"items": rows}))
    assert "…" in out
    assert "x" * 100 not in out


def _table_rows(out: str) -> list[str]:
    # Table lines are indented; the "(+N more fields…)" footer is prose, not a row.
    return [
        ln for ln in out.splitlines() if ln.startswith("  ") and not ln.lstrip().startswith("(+")
    ]


def test_render_human_table_fits_terminal_width(monkeypatch):
    # A wide table must shrink to fit the terminal so it doesn't wrap/corrupt.
    monkeypatch.setenv("COLUMNS", "60")
    rows = [
        {"id": f"prj-{'x' * 16}", "name": "y" * 40, "desc": "z" * 40, "vis": "private"}
        for _ in range(3)
    ]
    out = render_human(ok({"items": rows}))
    body = _table_rows(out)
    assert body, out
    assert all(len(ln) <= 60 for ln in body), max(body, key=len)


def test_render_human_table_drops_columns_when_too_narrow(monkeypatch):
    # When even floor-width columns can't all fit, the lowest-priority ones are
    # dropped (folded into the "more fields" note) so rows never exceed the width.
    monkeypatch.setenv("COLUMNS", "30")
    rows = [{f"c{i}": "x" * 10 for i in range(6)} for _ in range(2)]
    out = render_human(ok({"items": rows}))
    body = _table_rows(out)
    assert all(len(ln) <= 30 for ln in body), max(body, key=len)
    assert "more field" in out  # dropped columns are reported, not silently lost


def test_render_human_table_reports_dropped_nested_fields():
    # A flat table can't show nested dict/list fields. They must be counted in the
    # "+N more fields" note — otherwise they vanish with no hint (silent loss).
    rows = [{"id": "p1", "name": "Alpha", "members": [{"u": 1}], "meta": {"k": "v"}}]
    out = render_human(ok({"projects": rows}))
    assert "members" not in out  # nested field isn't shown in the flat table
    assert "(+2 more fields per row" in out  # but the user IS told 2 were dropped


def test_render_human_table_footer_fits_terminal_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "24")
    rows = [{"id": "p1", "name": "Alpha", "extra": {"nested": True}}]
    out = render_human(ok({"items": rows}))
    note = [ln for ln in out.splitlines() if "more field" in ln]
    assert note, out
    assert all(len(ln) <= 24 for ln in note), note[0]


def test_render_human_stream_resets_state_on_start(capsys):
    # A prior stream that ended mid-text must not bleed a stray newline into the
    # next stream's first discrete record.
    render_human_stream("text", {"text": "leftover"})
    capsys.readouterr()  # discard
    assert render_human_stream("start", {"command": "sumcli x"}) is None
    step = render_human_stream("step", {"name": "search", "status": "started"})
    assert step == "[started] search"  # no leading newline artifact
    assert capsys.readouterr().out == ""  # start consumed the pending flush cleanly


def test_render_human_error_includes_fix():
    out = render_human(err("NO_PROJECT", "No project specified.", "Pass --project."))
    assert out.startswith("Error [NO_PROJECT]: No project specified.")
    assert "Fix: Pass --project." in out


def test_render_human_next_actions():
    env = ok({"x": 1}, next_actions=[action("List projects", "sumcli projects list")])
    out = render_human(env)
    assert "Next:" in out
    assert "List projects" in out
    assert "$ sumcli projects list" in out


def test_render_human_stream_step_and_log():
    step = render_human_stream("step", {"name": "search", "status": "started"})
    assert step == "[started] search"
    assert render_human_stream("log", {"level": "error", "message": "boom"}) == "[error] boom"
    assert render_human_stream("start", {"command": "sumcli chats send"}) is None


def test_render_human_stream_malformed_error_is_not_silent():
    # An error record missing the `ok` envelope key must still surface text — a
    # blank render alongside a non-zero exit would be a silent failure.
    out = render_human_stream("error", {"message": "upstream exploded"})
    assert out and "upstream exploded" in out
    assert render_human_stream("error", {}), "empty error record must still render"


def test_resolve_output_mode_flag_wins(monkeypatch):
    monkeypatch.setenv("SUMCLI_OUTPUT", "json")
    assert resolve_output_mode("human") == "human"


def test_resolve_output_mode_env_fallback(monkeypatch):
    monkeypatch.setenv("SUMCLI_OUTPUT", "human")
    assert resolve_output_mode(None) == "human"


def test_resolve_output_mode_unknown_falls_through(monkeypatch):
    monkeypatch.delenv("SUMCLI_OUTPUT", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert resolve_output_mode("garbage") == "json"


@pytest.mark.parametrize("isatty,expected", [(True, "human"), (False, "json")])
def test_resolve_output_mode_tty_autodetect(monkeypatch, isatty, expected):
    monkeypatch.delenv("SUMCLI_OUTPUT", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: isatty)
    assert resolve_output_mode(None) == expected


def test_human_mode_survives_ascii_stdout(monkeypatch):
    # On an ASCII stdout the em-dash/ellipsis glyphs must degrade, not crash.
    ascii_out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr("sys.stdout", ascii_out)
    monkeypatch.setattr("sys.stderr", ascii_out)
    set_output_mode("human")
    assert ascii_out.errors == "replace"
    # render_human emits "—" for None; writing it must not raise.
    ascii_out.write(render_human(ok({"x": None})))
    ascii_out.flush()
    set_output_mode("json")  # restore the module default for other tests
