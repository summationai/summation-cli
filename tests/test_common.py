"""Tests for shared resource helpers."""

from __future__ import annotations

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
