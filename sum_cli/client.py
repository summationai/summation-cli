"""Thin HTTP client over sum-api."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from sum_cli import debug_log
from sum_cli.auth import TokenResult, acquire_token, token_cache_valid
from sum_cli.config import Config, load


class ApiError(RuntimeError):
    def __init__(
        self,
        status: int,
        body: Any,
        *,
        method: str | None = None,
        url: str | None = None,
    ):
        super().__init__(f"sum-api {status}: {body!r}")
        self.status = status
        self.body = body
        self.method = method
        self.url = url


def _token_cache_key(cfg: Config) -> tuple:
    """Key invalidates when credentials or persisted session change."""
    if cfg.device_login_credential:
        return (
            "device_login",
            cfg.profile,
            cfg.base_url,
            cfg.device_login_credential,
        )
    if cfg.file_access_token and cfg.token_expires_at is not None:
        return (
            "file",
            cfg.profile,
            cfg.base_url,
            cfg.client_id,
            cfg.client_secret,
            cfg.m2m_scope,
            cfg.file_access_token,
            cfg.token_expires_at,
        )
    return ("m2m", cfg.profile, cfg.base_url, cfg.client_id, cfg.client_secret, cfg.m2m_scope)


class Client:
    _token_cache: dict[tuple, TokenResult] = {}

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load()
        self._http = httpx.Client(timeout=30.0)

    @classmethod
    def clear_token_cache(cls) -> None:
        cls._token_cache.clear()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def token(self) -> str:
        """Return bearer token (cached per profile/base_url/credentials)."""
        return self._token_result().access_token

    def _token_result(self) -> TokenResult:
        key = _token_cache_key(self.cfg)
        cached = Client._token_cache.get(key)
        if cached is not None and token_cache_valid(cached.expires_at):
            debug_log.log_bearer_token(cached.access_token, operation="token(cache)")
            return cached
        debug_log.debug("acquiring token via %s", debug_log.token_source_label(self.cfg))
        result = acquire_token(self.cfg, self._http)
        Client._token_cache[key] = result
        debug_log.log_bearer_token(result.access_token, operation="token(acquired)")
        return result

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token_result().access_token}"}
        if extra:
            h.update(extra)
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.cfg.base_url}{path}"
        debug_log.log_http_request(method, url)
        resp = self._http.request(
            method,
            url,
            params=params,
            json=json,
            headers=self._headers(headers),
        )
        if resp.status_code >= 400:
            try:
                body: Any = resp.json()
            except ValueError:
                body = resp.text
            debug_log.log_http_response(
                method,
                url,
                status=resp.status_code,
                body=body,
                headers=dict(resp.headers),
            )
            raise ApiError(resp.status_code, body, method=method, url=url)
        debug_log.log_http_response(method, url, status=resp.status_code, body=None)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        url = f"{self.cfg.base_url}{path}"
        resp = self._http.request(
            method,
            url,
            params=params,
            content=content,
            headers=self._headers(headers),
        )
        if resp.status_code >= 400:
            try:
                body: Any = resp.json()
            except ValueError:
                body = resp.text
            raise ApiError(resp.status_code, body)
        return resp.content

    def put_url(
        self, url: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        resp = self._http.put(url, content=data, headers={"Content-Type": content_type})
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)

    @contextmanager
    def stream(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[httpx.Response]:
        url = f"{self.cfg.base_url}{path}"
        with self._http.stream(
            method,
            url,
            params=params,
            json=json,
            headers=self._headers(headers),
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                try:
                    body: Any = resp.json()
                except ValueError:
                    body = resp.text
                raise ApiError(resp.status_code, body)
            yield resp
