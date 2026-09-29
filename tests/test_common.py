"""Tests for shared resource helpers."""

from __future__ import annotations

import json

import pytest

from sum_cli.commands import extract_list, unwrap_data


def test_unwrap_data_missing_key_returns_none() -> None:
    assert unwrap_data({"data": {"x": 1}}, "data", "missing") is None


def test_unwrap_data_drills_nested() -> None:
    assert unwrap_data({"data": {"x": 1}}, "data", "x") == 1


def test_unwrap_data_non_dict_stops() -> None:
    assert unwrap_data({"data": "plain"}, "data", "x") is None


def test_extract_list_from_list() -> None:
    assert extract_list([1, 2], "items") == [1, 2]


def test_extract_list_from_dict_key() -> None:
    assert extract_list({"chats": [{"id": "a"}]}, "chats") == [{"id": "a"}]


def test_extract_list_second_key_matches() -> None:
    assert extract_list({"connectors": [{"id": "a"}]}, "connections", "connectors") == [{"id": "a"}]


def test_extract_list_empty_dict_is_zero_results() -> None:
    """An empty payload is a genuine zero, not drift."""
    assert extract_list({}, "connections", "connectors") == []


def test_extract_list_recognized_key_with_empty_list() -> None:
    assert extract_list({"connections": [], "total": 0}, "connections", "connectors") == []


def test_extract_list_non_dict_non_list_is_empty() -> None:
    assert extract_list(None, "connections") == []


def test_extract_list_unknown_dict_shape_raises() -> None:
    """The SUM-5882 failure mode: a shape sumcli cannot read must not read as zero."""
    with pytest.raises(SystemExit) as exc:
        extract_list({"items": [{"id": "a"}], "total": 1}, "connections", "connectors")
    assert exc.value.code == 1


def test_extract_list_unknown_dict_shape_names_expected_and_seen_keys(capsys) -> None:
    with pytest.raises(SystemExit):
        extract_list({"items": [], "nextPage": "x"}, "connections", "connectors")
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "UNEXPECTED_SHAPE"
    # Both halves matter: what sumcli looked for, and what the API actually sent.
    assert "connections, connectors" in payload["error"]["message"]
    assert "items, nextPage" in payload["error"]["message"]


def test_extract_list_non_list_value_under_recognized_key_raises() -> None:
    """A recognized key holding a non-list is drift too, not an empty result."""
    with pytest.raises(SystemExit):
        extract_list({"connections": {"a": 1}}, "connections")
