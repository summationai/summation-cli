"""SharePoint backend for the filesystem protocol (Microsoft Graph, app-only).

Authenticates with the OAuth2 *client credentials* grant against the app's
Azure AD tenant, then drives Microsoft Graph's drive/item API. App-only auth
requires the application permissions ``Sites.Read.All`` (read) and
``Files.ReadWrite.All`` (upload/delete) to be admin-consented for the app.

Credentials and defaults are read from ``~/.summation/summation-config`` first, then
environment variables (env is mainly for ``import-env`` input and temporary overrides)::

    SHAREPOINT_TENANT_ID        Azure AD tenant id (the app's home tenant)
    SHAREPOINT_CLIENT_ID        app (client) id        — falls back to CLIENT_ID
    SHAREPOINT_CLIENT_SECRET    app client secret      — falls back to CLIENT_SECRET
    SHAREPOINT_SITE_URL         site to operate on (required), as "<host>:/sites/<name>"
                                or a full https URL.
    SHAREPOINT_ROOT             default drive id (optional; quote in shell/.env if it contains ``!``)
    SHAREPOINT_PATH             default folder item id (optional)

Import from a skill-style env file::

    sumcli filesystem import-env .env --provider sharepoint

Persisted settings in ``~/.summation/summation-config``::

    [sharepoint]
    tenant_id = "..."
    client_id = "..."
    client_secret = "..."
    site_url = "..."

    [filesystem]
    sharepoint_root = "..."
    sharepoint_path = "..."

Set defaults alone with ``sumcli filesystem set-defaults``.

Roots are the document libraries (Graph *drives*) on that site.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from sum_cli.config_store import config_path, read_all, redact, write_all
from sum_cli.filesystem.base import FsEntry, FsListResult, FsRoot
from sum_cli.filesystem.config_defaults import env_default, read_filesystem_default
from sum_cli.filesystem.registry import FileSystemError

SHAREPOINT_SECTION = "sharepoint"

_ENV_TO_CONFIG = {
    "SHAREPOINT_TENANT_ID": "tenant_id",
    "SHAREPOINT_CLIENT_ID": "client_id",
    "SHAREPOINT_CLIENT_SECRET": "client_secret",
    "SHAREPOINT_SITE_URL": "site_url",
}

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY = "https://login.microsoftonline.com"
_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_SKEW_SECONDS = 60


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _missing(var: str, fix: str) -> FileSystemError:
    return FileSystemError("MISSING_CREDENTIALS", f"{var} is not set.", fix)


def _graph_id(resource_id: str) -> str:
    """URL-encode a drive or item id for Graph path segments (ids often contain ``!``)."""
    return quote(resource_id, safe="-")


def _normalize_site(raw: str) -> str:
    """Return Graph's ``{host}:{server-relative-path}`` site addressing form.

    Accepts either that form directly (``host:/sites/Name``) or a full
    ``https://host/sites/Name/...`` URL and reduces it to ``host:/sites/Name``.
    """
    if raw.startswith(("http://", "https://")):
        parts = urlsplit(raw)
        segments = [s for s in parts.path.split("/") if s]
        # Keep the leading "sites"/"teams" container + its name; drop deep paths.
        if len(segments) >= 2 and segments[0] in ("sites", "teams"):
            server_relative = f"/{segments[0]}/{segments[1]}"
        else:
            server_relative = "/" + "/".join(segments)
        return f"{parts.netloc}:{server_relative}"
    return raw


@dataclass
class _Token:
    value: str
    expires_at: float  # unix epoch seconds


class SharePointFileSystem:
    """Microsoft Graph–backed :class:`~sum_cli.filesystem.base.FileSystem`."""

    provider = "sharepoint"

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        site: str,
        http: httpx.Client | None = None,
    ):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._site = _normalize_site(site)
        self._http = http or httpx.Client(timeout=30.0)
        self._token: _Token | None = None
        self._site_id: str | None = None
        self._drives_cache: list[FsRoot] | None = None

    @classmethod
    def from_env(cls) -> "SharePointFileSystem":
        cfg = read_sharepoint_config()
        tenant = cfg.get("tenant_id") or _env("SHAREPOINT_TENANT_ID")
        if not tenant:
            raise _missing(
                "SHAREPOINT_TENANT_ID",
                "Set SHAREPOINT_TENANT_ID or run: sumcli filesystem import-env <file> --provider sharepoint",
            )
        client_id = cfg.get("client_id") or _env("SHAREPOINT_CLIENT_ID", "CLIENT_ID")
        if not client_id:
            raise _missing(
                "SHAREPOINT_CLIENT_ID",
                "Set SHAREPOINT_CLIENT_ID (or CLIENT_ID) or run: sumcli filesystem import-env <file> --provider sharepoint",
            )
        secret = cfg.get("client_secret") or _env("SHAREPOINT_CLIENT_SECRET", "CLIENT_SECRET")
        if not secret:
            raise _missing(
                "SHAREPOINT_CLIENT_SECRET",
                "Set SHAREPOINT_CLIENT_SECRET (or CLIENT_SECRET) or run: sumcli filesystem import-env <file> --provider sharepoint",
            )
        site = cfg.get("site_url") or _env("SHAREPOINT_SITE_URL")
        if not site:
            raise _missing(
                "SHAREPOINT_SITE_URL",
                "Set SHAREPOINT_SITE_URL or run: sumcli filesystem import-env <file> --provider sharepoint",
            )
        return cls(tenant_id=tenant, client_id=client_id, client_secret=secret, site=site)

    def default_root(self) -> str | None:
        return read_filesystem_default(self.provider, "root") or env_default(self.provider, "root")

    def default_path(self) -> str | None:
        return read_filesystem_default(self.provider, "path") or env_default(self.provider, "path")

    def close(self) -> None:
        self._http.close()

    # -- auth ---------------------------------------------------------------

    def _bearer(self) -> str:
        now = time.time()
        if self._token and now < self._token.expires_at - _TOKEN_SKEW_SECONDS:
            return self._token.value
        url = f"{_AUTHORITY}/{self._tenant_id}/oauth2/v2.0/token"
        resp = self._http.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": _SCOPE,
            },
        )
        if resp.status_code >= 400:
            raise FileSystemError(
                "AUTH_FAILED",
                f"Token request failed ({resp.status_code}): {_err_text(resp)}",
                "Verify SHAREPOINT_TENANT_ID, the client id/secret, and that the secret "
                "has not expired in Azure AD.",
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise FileSystemError(
                "AUTH_FAILED",
                f"Token response missing access_token: {body!r}",
                "Check the app registration's client-credentials configuration.",
            )
        self._token = _Token(value=token, expires_at=now + float(body.get("expires_in", 3600)))
        return token

    def _graph(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        merged_headers = {"Authorization": f"Bearer {self._bearer()}"}
        if headers:
            merged_headers.update(headers)
        resp = self._http.request(
            method,
            f"{GRAPH_BASE}{path}",
            params=params,
            headers=merged_headers,
            **kwargs,
        )
        if resp.status_code >= 400:
            raise FileSystemError(
                "GRAPH_ERROR",
                f"Graph {method} {path} failed ({resp.status_code}): {_err_text(resp)}",
                "Confirm the app has admin-consented Sites.Read.All / Files.ReadWrite.All "
                "and that the site/root/item ids are correct.",
            )
        return resp

    def _site_ref(self) -> str:
        if self._site_id is None:
            self._site_id = str(self._graph("GET", f"/sites/{self._site}").json()["id"])
        return self._site_id

    # -- protocol -----------------------------------------------------------

    def roots(self) -> list[FsRoot]:
        if self._drives_cache is not None:
            return self._drives_cache
        data = self._graph("GET", f"/sites/{self._site_ref()}/drives").json()
        self._drives_cache = [
            FsRoot(id=d["id"], name=d.get("name", ""), raw=d) for d in data.get("value", [])
        ]
        return self._drives_cache

    def list(self, *, root: str, path: str | None = None, limit: int) -> FsListResult:
        drive = _graph_id(root)
        if path:
            endpoint = f"/drives/{drive}/items/{_graph_id(path)}/children"
        else:
            endpoint = f"/drives/{drive}/root/children"
        entries: list[FsEntry] = []
        params: dict | None = {"$top": min(limit, 200)}
        next_path: str | None = endpoint
        truncated = False
        while next_path and len(entries) < limit:
            resp = self._graph("GET", next_path, params=params).json()
            batch = resp.get("value", [])
            for item in batch:
                if len(entries) >= limit:
                    truncated = True
                    break
                entries.append(_to_entry(item))
            link = resp.get("@odata.nextLink")
            next_path = link[len(GRAPH_BASE) :] if link and link.startswith(GRAPH_BASE) else None
            if next_path is not None:
                truncated = True
            params = None
            if len(entries) >= limit:
                break
        return FsListResult(entries=entries, truncated=truncated)

    def download(self, *, root: str, item: str) -> Iterator[bytes]:
        # Graph's /content endpoint 302s to a short-lived, pre-authenticated
        # download URL; without follow_redirects httpx would hand back the empty
        # redirect body instead of the file.
        with self._http.stream(
            "GET",
            f"{GRAPH_BASE}/drives/{_graph_id(root)}/items/{_graph_id(item)}/content",
            headers={"Authorization": f"Bearer {self._bearer()}"},
            follow_redirects=True,
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise FileSystemError(
                    "GRAPH_ERROR",
                    f"Download of item {item} failed ({resp.status_code}): {_err_text(resp)}",
                    "Verify the root (drive) id and item id with `sumcli filesystem list`.",
                )
            yield from resp.iter_bytes()

    def upload(self, *, root: str, parent: str | None, name: str, data: bytes) -> FsEntry:
        # Simple upload (<= ~250 MB). The colon path syntax addresses the new
        # file by name under the parent folder (or root) without a prior lookup.
        drive = _graph_id(root)
        anchor = f"items/{_graph_id(parent)}" if parent else "root"
        path = f"/drives/{drive}/{anchor}:/{quote(name, safe='')}:/content"
        item = self._graph(
            "PUT", path, content=data, headers={"Content-Type": "application/octet-stream"}
        ).json()
        return _to_entry(item)

    def mkdir(self, *, root: str, parent: str | None, name: str) -> FsEntry:
        drive = _graph_id(root)
        anchor = f"items/{_graph_id(parent)}" if parent else "root"
        item = self._graph(
            "POST",
            f"/drives/{drive}/{anchor}/children",
            json={
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        ).json()
        return _to_entry(item)

    def delete(self, *, root: str, item: str) -> None:
        self._graph("DELETE", f"/drives/{_graph_id(root)}/items/{_graph_id(item)}")


def _err_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message") or body["error"])
    return str(body)[:300]


def _to_entry(item: dict) -> FsEntry:
    return FsEntry(
        id=item.get("id", ""),
        name=item.get("name", ""),
        kind="folder" if "folder" in item else "file",
        size=item.get("size"),
        path=(item.get("parentReference") or {}).get("path"),
        modified=item.get("lastModifiedDateTime"),
        raw=item,
    )


def read_sharepoint_config() -> dict[str, str]:
    return dict(read_all().get(SHAREPOINT_SECTION, {}))


def sharepoint_section_from_env(env: dict[str, str]) -> dict[str, str]:
    section: dict[str, str] = {}
    for env_key, config_key in _ENV_TO_CONFIG.items():
        value = env.get(env_key)
        if value:
            section[config_key] = value
    return section


def write_sharepoint_config(section: dict[str, str]) -> Path:
    p = config_path()
    data = read_all(p)
    existing = data.setdefault(SHAREPOINT_SECTION, {})
    existing.update(section)
    write_all(p, data)
    return p


def required_sharepoint_fields_missing(section: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for env_key, config_key in _ENV_TO_CONFIG.items():
        if not section.get(config_key):
            missing.append(env_key)
    return missing


def public_sharepoint_config(section: dict[str, str]) -> dict[str, str | None]:
    return {
        "tenant_id": section.get("tenant_id"),
        "client_id": section.get("client_id"),
        "client_secret": redact(section["client_secret"]) if section.get("client_secret") else None,
        "site_url": section.get("site_url"),
    }
