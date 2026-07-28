"""Read/write ~/.summation/config including active profile and per-profile fields."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

from sum_cli.constants import ACTIVE_PROFILE_KEY, META_SECTION

DEFAULT_CONFIG_PATH = Path.home() / ".summation" / "config"


def config_path() -> Path:
    return Path(os.environ.get("SUMMATION_CONFIG_FILE", DEFAULT_CONFIG_PATH))


def read_all(path: Path | None = None) -> dict[str, dict[str, str]]:
    p = path or config_path()
    if not p.exists():
        return {}
    with p.open("rb") as f:
        data = tomllib.load(f)
    out: dict[str, dict[str, str]] = {}
    for section, values in data.items():
        if section == META_SECTION:
            if isinstance(values, dict):
                out[META_SECTION] = {k: str(v) for k, v in values.items()}
            continue
        if isinstance(values, dict):
            out[str(section)] = {k: str(v) for k, v in values.items() if isinstance(v, (str, int))}
    return out


def write_all(path: Path, data: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if META_SECTION in data:
        lines.append(f"[{META_SECTION}]")
        for key, value in data[META_SECTION].items():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        lines.append("")
    for section, values in data.items():
        if section == META_SECTION:
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def get_active_profile_name(data: dict[str, dict[str, str]] | None = None) -> str | None:
    d = data if data is not None else read_all()
    meta = d.get(META_SECTION, {})
    return meta.get(ACTIVE_PROFILE_KEY)


def set_active_profile(name: str) -> Path:
    path = config_path()
    data = read_all(path)
    if name not in data or name == META_SECTION:
        raise KeyError(name)
    data.setdefault(META_SECTION, {})[ACTIVE_PROFILE_KEY] = name
    write_all(path, data)
    return path


def update_profile_field(profile: str, **fields: str | None) -> Path:
    path = config_path()
    data = read_all(path)
    section = data.setdefault(profile, {})
    for k, v in fields.items():
        if v is None or v == "":
            section.pop(k, None)
        else:
            section[k] = v
    write_all(path, data)
    return path


def redact(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]}"
