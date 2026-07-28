"""Configuration resolution for sumcli."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sum_cli.config_store import (
    DEFAULT_CONFIG_PATH,
    get_active_profile_name,
    read_all,
)
from sum_cli.constants import DEVICE_LOGIN_CREDENTIAL_KEY, META_SECTION, TOKEN_EXPIRES_AT_KEY

DEFAULT_BASE_URL = "https://sandbox-api.summation.com"
DEFAULT_PROFILE = "default"


@dataclass(frozen=True)
class Config:
    base_url: str
    access_token: str | None
    device_login_credential: str | None
    client_id: str | None
    client_secret: str | None
    m2m_scope: str | None
    profile: str
    default_project: str | None
    source: str
    file_access_token: str | None = None
    file_device_login_credential: str | None = None
    token_expires_at: float | None = None

    @property
    def has_m2m(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def has_device_login(self) -> bool:
        return bool(self.device_login_credential)


def _normalize_base_url(url: str) -> str:
    return url.strip().rstrip("/")


def _parse_token_expires_at(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def load(
    *,
    profile: str | None = None,
    base_url: str | None = None,
    config_file: Path | str | None = None,
) -> Config:
    env = os.environ
    path = Path(config_file or env.get("SUMMATION_CONFIG_FILE") or DEFAULT_CONFIG_PATH)
    all_data = read_all(path)

    resolved_profile = (
        profile
        or env.get("SUMMATION_PROFILE")
        or get_active_profile_name(all_data)
        or DEFAULT_PROFILE
    )

    file_values = all_data.get(resolved_profile, {})
    if resolved_profile == META_SECTION:
        file_values = {}

    # Credentials come only from the profile (or `--base-url`). SUM_API_* env vars are an
    # input to `config import-env`, never a live override on a selected profile — otherwise
    # a stray .env silently hijacks whichever profile you pick.
    resolved_base = _normalize_base_url(base_url or file_values.get("base_url") or DEFAULT_BASE_URL)

    file_access_token = file_values.get("access_token")
    file_device_login_credential = file_values.get(DEVICE_LOGIN_CREDENTIAL_KEY)
    token_expires_at = _parse_token_expires_at(file_values.get(TOKEN_EXPIRES_AT_KEY))

    sources: list[str] = []
    if file_values:
        sources.append(f"file:{path}#{resolved_profile}")
    if profile or base_url or config_file:
        sources.append("cli")

    return Config(
        base_url=resolved_base,
        access_token=file_values.get("access_token"),
        device_login_credential=file_device_login_credential,
        client_id=file_values.get("client_id"),
        client_secret=file_values.get("client_secret"),
        m2m_scope=file_values.get("m2m_scope"),
        profile=resolved_profile,
        default_project=file_values.get("default_project") or env.get("SUMMATION_PROJECT"),
        source=",".join(sources) if sources else "defaults",
        file_access_token=file_access_token,
        file_device_login_credential=file_device_login_credential,
        token_expires_at=token_expires_at,
    )
