"""Profile naming conventions and account summaries for CLI envelopes."""

from __future__ import annotations

from sum_cli.config import Config


def parse_profile_account(name: str) -> dict[str, str | None]:
    """Split ``{tenant}_{env}`` on the last underscore for display."""
    if "_" in name:
        tenant, environment = name.rsplit("_", 1)
        return {"tenant": tenant, "environment": environment}
    return {"tenant": name, "environment": None}


def account_summary(cfg: Config, *, default_project: str | None = None) -> dict:
    parsed = parse_profile_account(cfg.profile)
    project = default_project if default_project is not None else cfg.default_project
    return {
        "profile": cfg.profile,
        "tenant": parsed["tenant"],
        "environment": parsed["environment"],
        "base_url": cfg.base_url,
        "default_project": project,
    }


def profile_list_item(
    name: str,
    section: dict[str, str],
    *,
    active: bool,
) -> dict:
    parsed = parse_profile_account(name)
    return {
        "name": name,
        "active": active,
        "base_url": section.get("base_url"),
        "tenant": parsed["tenant"],
        "environment": parsed["environment"],
        "default_project": section.get("default_project"),
    }
