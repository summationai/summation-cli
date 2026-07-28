"""Shared constants for config and output."""

from __future__ import annotations

META_SECTION = "_meta"
ACTIVE_PROFILE_KEY = "active_profile"
DEVICE_LOGIN_CREDENTIAL_KEY = "device_login_credential"
SECRET_KEYS = frozenset({"client_secret", "access_token", DEVICE_LOGIN_CREDENTIAL_KEY})
TOKEN_EXPIRES_AT_KEY = "token_expires_at"

# Stytch M2M OAuth token lifetime when response omits expires_in (seconds).
DEFAULT_M2M_TTL_SECONDS = 300
TOKEN_CACHE_SKEW_SECONDS = 60
