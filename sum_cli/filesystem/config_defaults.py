"""Persist and resolve filesystem defaults in the shared Summation config."""

from __future__ import annotations

import os
from pathlib import Path

from sum_cli.config_store import config_path, read_all, write_all
from sum_cli.filesystem.base import FileSystem
from sum_cli.filesystem.registry import FileSystemError

FILESYSTEM_SECTION = "filesystem"


def _field_key(provider: str, field: str) -> str:
    return f"{provider}_{field}"


def read_filesystem_default(provider: str, field: str) -> str | None:
    """Read a persisted default (root or path) for a provider."""
    section = read_all().get(FILESYSTEM_SECTION, {})
    value = section.get(_field_key(provider, field))
    return value if value else None


def read_filesystem_defaults(provider: str) -> dict[str, str | None]:
    return {
        "root": read_filesystem_default(provider, "root"),
        "path": read_filesystem_default(provider, "path"),
    }


def set_filesystem_defaults(
    provider: str,
    *,
    root: str | None = None,
    path: str | None = None,
) -> Path:
    """Merge root/path into [filesystem]; only keys passed as non-None are updated."""
    p = config_path()
    data = read_all(p)
    section = data.setdefault(FILESYSTEM_SECTION, {})
    if root is not None:
        section[_field_key(provider, "root")] = root
    if path is not None:
        section[_field_key(provider, "path")] = path
    write_all(p, data)
    return p


def env_default(provider: str, field: str) -> str | None:
    """Provider-specific env fallback (e.g. SHAREPOINT_ROOT). Used after config file."""
    if provider == "sharepoint":
        env_name = f"SHAREPOINT_{field.upper()}"
        value = os.environ.get(env_name)
        return value if value else None
    return None


def effective_filesystem_defaults(provider: str) -> dict[str, str | None]:
    """Config file beats env for each field (CLI flags beat both elsewhere)."""
    persisted = read_filesystem_defaults(provider)
    return {
        "root": persisted["root"] or env_default(provider, "root"),
        "path": persisted["path"] or env_default(provider, "path"),
    }


def _provider_default(fs: FileSystem, method: str) -> str | None:
    fn = getattr(fs, method, None)
    if not callable(fn):
        return None
    value = fn()
    return value if isinstance(value, str) and value else None


def resolve_root(fs: FileSystem, explicit: str | None) -> str:
    """Resolve drive/root id for a command.

    Precedence (highest first):
      1. CLI flag (--root)
      2. ~/.summation/summation-config [filesystem] (``sumcli filesystem set-defaults``)
      3. Provider env (e.g. SHAREPOINT_ROOT)
      4. Auto-pick when ``roots()`` returns exactly one drive (cached per backend instance)
    """
    if explicit:
        return explicit
    from_default = _provider_default(fs, "default_root")
    if from_default:
        return from_default
    roots = fs.roots()
    if len(roots) == 1:
        return roots[0].id
    if not roots:
        raise FileSystemError(
            "NO_ROOT",
            "No drives found on the configured site.",
            "Check SHAREPOINT_SITE_URL and app permissions (Sites.Read.All).",
        )
    names = ", ".join(f"{r.name!r} ({r.id})" for r in roots[:5])
    extra = f" (+{len(roots) - 5} more)" if len(roots) > 5 else ""
    raise FileSystemError(
        "NO_ROOT",
        f"Multiple drives ({len(roots)}); specify --root, set SHAREPOINT_ROOT, or run "
        f"`sumcli filesystem set-defaults --provider {fs.provider} --root <id>`.",
        f"Available: {names}{extra}. Run `sumcli filesystem roots --provider {fs.provider}`.",
    )


def resolve_path(fs: FileSystem, explicit: str | None) -> str | None:
    """Resolve folder id; None means drive root level.

    Precedence: CLI --path, then config [filesystem], then provider env.
    """
    if explicit:
        return explicit
    return _provider_default(fs, "default_path")
