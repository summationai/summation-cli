"""Resolve default project ID for the active profile."""

from __future__ import annotations

from sum_cli.config import Config, load


def resolve_project(
    cfg: Config | None = None,
    *,
    explicit: str | None = None,
) -> str | None:
    if explicit:
        return explicit
    cfg = cfg or load()
    return cfg.default_project
