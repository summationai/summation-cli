"""Parse env files with SUM_API_* keys and map them to sumcli profile fields."""

from __future__ import annotations

from pathlib import Path

from sum_cli.config import DEFAULT_BASE_URL, _normalize_base_url

_ENV_TO_PROFILE = {
    "SUM_API_BASE_URL": "base_url",
    "SUM_API_CLIENT_ID": "client_id",
    "SUM_API_CLIENT_SECRET": "client_secret",
    "SUM_API_M2M_SCOPE": "m2m_scope",
    "SUM_API_ACCESS_TOKEN": "access_token",
}

_ACTIVE_PROFILE_KEY = "SUM_API_ACTIVE_PROFILE"
_SECTION_PREFIX = "profile."


class EnvImportError(Exception):
    """A sectioned env file cannot be resolved to a single profile."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _parse_env_lines(path: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    globals_: dict[str, str] = {}
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] = globals_

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            if name.startswith(_SECTION_PREFIX):
                name = name[len(_SECTION_PREFIX) :]
            current = sections.setdefault(name, {})
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        current[key] = value

    return globals_, sections


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse an env file into a single profile's ``SUM_API_*`` keys.

    Flat files (no ``[profile.NAME]`` headers) return their keys directly.
    Sectioned files honor ``SUM_API_ACTIVE_PROFILE`` and return that section's
    keys (with file-level keys filling gaps). If a sectioned file has no active
    marker, or the marker names a missing section, raise ``EnvImportError`` so
    the caller surfaces a clear error rather than silently picking a profile.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))

    globals_, sections = _parse_env_lines(path)

    if not sections:
        return globals_

    active = globals_.get(_ACTIVE_PROFILE_KEY)
    if not active:
        raise EnvImportError(
            "ACTIVE_PROFILE_REQUIRED",
            f"{path} declares profile sections but no {_ACTIVE_PROFILE_KEY}.",
            f"Set {_ACTIVE_PROFILE_KEY} to one of: {', '.join(sorted(sections))}.",
        )
    if active not in sections:
        raise EnvImportError(
            "ACTIVE_PROFILE_NOT_FOUND",
            f"{_ACTIVE_PROFILE_KEY}={active} has no [profile.{active}] section in {path}.",
            f"Available sections: {', '.join(sorted(sections))}.",
        )
    return {**globals_, **sections[active]}


def profile_section_from_env(env: dict[str, str]) -> dict[str, str]:
    """Map ``SUM_API_*`` keys to TOML profile section fields."""
    section: dict[str, str] = {}
    for env_key, profile_key in _ENV_TO_PROFILE.items():
        raw = env.get(env_key)
        if raw is None or raw == "":
            continue
        if profile_key == "base_url":
            section[profile_key] = _normalize_base_url(raw)
        elif profile_key in {"client_id", "client_secret", "m2m_scope", "access_token"}:
            section[profile_key] = raw.strip() if profile_key == "client_id" else raw
        else:
            section[profile_key] = raw
    if "base_url" not in section:
        section["base_url"] = DEFAULT_BASE_URL
    return section


def required_fields_present(section: dict[str, str]) -> list[str]:
    missing = []
    if not section.get("client_id"):
        missing.append("SUM_API_CLIENT_ID")
    if not section.get("client_secret"):
        missing.append("SUM_API_CLIENT_SECRET")
    return missing
