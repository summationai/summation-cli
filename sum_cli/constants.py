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

# sum-api read/write budget. grid create / tables upsert often exceed 30s while
# the server is still working; a client timeout here looks like failure and
# invites a duplicate retry. Connect stays short so a dead host fails fast.
DEFAULT_HTTP_TIMEOUT_SECONDS = 120.0
DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0
MAX_HTTP_TIMEOUT_SECONDS = 3600.0
