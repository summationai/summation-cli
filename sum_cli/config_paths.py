"""Config file path resolution (canonical ``~/.summation/summation-config``)."""

from __future__ import annotations

import os
from pathlib import Path

from sum_cli.config_store import DEFAULT_CONFIG_PATH

# Previous sumcli releases used this filename (same TOML format). Migrated once on read.
_LEGACY_CONFIG_PATH = Path.home() / ".summation" / "config"


def resolve_config_path() -> Path:
    """Return the config file sumcli reads and writes.

    Honors ``SUMMATION_CONFIG_FILE``. Otherwise uses
    ``~/.summation/summation-config``, auto-renaming a legacy ``~/.summation/config``
    file when present and the canonical path does not exist yet.
    """
    explicit = os.environ.get("SUMMATION_CONFIG_FILE")
    if explicit:
        return Path(explicit)
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    if _LEGACY_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LEGACY_CONFIG_PATH.rename(DEFAULT_CONFIG_PATH)
        return DEFAULT_CONFIG_PATH
    return DEFAULT_CONFIG_PATH
