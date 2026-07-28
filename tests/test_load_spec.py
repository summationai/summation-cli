"""Bundled OpenAPI snapshot tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from sum_cli.openapi_doc import (
    SNAPSHOT_PATH,
    OpenApiSpecError,
    load_spec,
)


def test_load_spec_reads_bundled_snapshot() -> None:
    spec = load_spec()
    assert isinstance(spec.get("paths"), dict)
    assert "/v1/projects" in spec["paths"]


def test_bundled_snapshot_file_exists_in_source_tree() -> None:
    assert SNAPSHOT_PATH.is_file(), f"missing bundled snapshot at {SNAPSHOT_PATH}"


def test_load_spec_missing_custom_path_raises() -> None:
    with pytest.raises(OpenApiSpecError, match="Cannot read OpenAPI snapshot"):
        load_spec("/nonexistent/openapi_snapshot.json")


def test_load_spec_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(OpenApiSpecError, match="not valid JSON"):
        load_spec(str(bad))
