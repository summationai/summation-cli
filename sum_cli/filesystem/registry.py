"""Backend registry and shared errors for the filesystem protocol.

``get_filesystem(provider)`` resolves a provider slug to a ready-to-use
:class:`~sum_cli.filesystem.base.FileSystem`. Each backend reads its own
credentials when constructed, so a missing-credential failure surfaces as a
:class:`FileSystemError` here rather than deep inside a request.
"""

from __future__ import annotations

from collections.abc import Callable

from sum_cli.filesystem.base import FileSystem

# Provider slugs accepted by ``--provider``. Order is display order.
PROVIDERS: list[str] = ["sharepoint"]


class FileSystemError(RuntimeError):
    """Base error for filesystem backends.

    ``code`` maps to the CLI error envelope code; ``fix`` is the plain-language
    remediation shown to the caller.
    """

    def __init__(self, code: str, message: str, fix: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix


class UnknownProvider(FileSystemError):
    def __init__(self, provider: str | None):
        super().__init__(
            "UNKNOWN_PROVIDER",
            f"Unknown filesystem provider {provider!r}."
            if provider
            else "No filesystem provider specified.",
            f"Pass --provider with one of: {', '.join(PROVIDERS)}.",
        )


def _make_sharepoint() -> FileSystem:
    # Imported lazily so a backend's optional deps / env reads only happen when
    # that provider is actually selected.
    from sum_cli.filesystem.sharepoint import SharePointFileSystem

    return SharePointFileSystem.from_env()


_FACTORIES: dict[str, Callable[[], FileSystem]] = {
    "sharepoint": _make_sharepoint,
}


def get_filesystem(provider: str | None) -> FileSystem:
    """Resolve a provider slug to a constructed backend.

    Raises :class:`UnknownProvider` for an unknown/empty slug, or
    :class:`FileSystemError` if the backend cannot be constructed (e.g. missing
    credentials).
    """
    factory = _FACTORIES.get(provider or "")
    if factory is None:
        raise UnknownProvider(provider)
    return factory()
