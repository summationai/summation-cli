"""Import SharePoint settings from a skill-style env file into ~/.summation/config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sum_cli.env_import import parse_env_file
from sum_cli.filesystem.config_defaults import set_filesystem_defaults
from sum_cli.filesystem.sharepoint import (
    public_sharepoint_config,
    required_sharepoint_fields_missing,
    sharepoint_section_from_env,
    write_sharepoint_config,
)


@dataclass(frozen=True)
class SharePointImportResult:
    config_path: Path
    imported_from: Path
    credentials: dict[str, str | None]
    defaults: dict[str, str]


def import_sharepoint_from_env_file(env_file: Path) -> SharePointImportResult:
    raw = parse_env_file(env_file.expanduser())
    credentials = sharepoint_section_from_env(raw)
    defaults: dict[str, str] = {}
    root = raw.get("SHAREPOINT_ROOT")
    if root:
        defaults["root"] = root
    path = raw.get("SHAREPOINT_PATH")
    if path:
        defaults["path"] = path

    if not credentials and not defaults:
        raise ValueError(
            "No SharePoint keys found. Expected SHAREPOINT_TENANT_ID, "
            "SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_SITE_URL, "
            "and/or SHAREPOINT_ROOT / SHAREPOINT_PATH."
        )

    if credentials:
        missing = required_sharepoint_fields_missing(credentials)
        if missing:
            raise ValueError(
                f"Incomplete SharePoint credentials; missing: {', '.join(missing)}. "
                "Include all of SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID, "
                "SHAREPOINT_CLIENT_SECRET, and SHAREPOINT_SITE_URL."
            )

    config_path: Path | None = None
    if credentials:
        config_path = write_sharepoint_config(credentials)
    if defaults:
        config_path = set_filesystem_defaults("sharepoint", **defaults)

    assert config_path is not None

    return SharePointImportResult(
        config_path=config_path,
        imported_from=env_file.expanduser(),
        credentials=public_sharepoint_config(credentials),
        defaults=defaults,
    )
