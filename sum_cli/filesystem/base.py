"""The provider-agnostic filesystem protocol.

A ``FileSystem`` is the contract every external storage backend implements:
SharePoint (first), then S3, Box, etc. Resource commands depend only on this
protocol and the two value types below, so adding a backend never touches the
CLI layer — implement the protocol and register it in
:mod:`sum_cli.filesystem.registry`.

Naming maps onto each backend's native hierarchy:

    root   — the top-level container you operate within.
             SharePoint: a *drive* (document library) on a site.
             S3:         a *bucket*.
             Box:        the account root / a folder tree's top.
    entry  — a file or folder inside a root, addressed by a backend-native id.

Ids are opaque strings, not paths. ``list`` returns ids you pass back to
``list`` (to descend into folders), ``download``, and ``delete``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FsRoot:
    """A top-level container (SharePoint drive, S3 bucket, ...)."""

    id: str
    name: str
    raw: dict | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True)
class FsListResult:
    """Paginated listing result from :meth:`FileSystem.list`."""

    entries: list[FsEntry]
    truncated: bool = False


@dataclass(frozen=True)
class FsEntry:
    """A file or folder within a root."""

    id: str
    name: str
    kind: str  # "file" | "folder"
    size: int | None = None
    path: str | None = None  # human-readable path within the root, if known
    modified: str | None = None  # ISO-8601, if known
    raw: dict | None = field(default=None, repr=False)

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "path": self.path,
            "modified": self.modified,
        }


@runtime_checkable
class FileSystem(Protocol):
    """Provider-agnostic external storage backend.

    Implementations own their HTTP client and credential resolution. Methods
    raise :class:`sum_cli.filesystem.registry.FileSystemError` (or a subclass)
    on failure so the resource layer can translate to a uniform error envelope.
    """

    provider: str  # registry slug, e.g. "sharepoint"

    def roots(self) -> list[FsRoot]:
        """List top-level containers available to the configured identity."""
        ...

    def list(self, *, root: str, path: str | None = None, limit: int) -> FsListResult:
        """List entries in ``root``; ``path`` is a folder id (None = root level)."""
        ...

    def download(self, *, root: str, item: str) -> Iterator[bytes]:
        """Stream the bytes of file ``item`` within ``root``."""
        ...

    def upload(self, *, root: str, parent: str | None, name: str, data: bytes) -> FsEntry:
        """Upload ``data`` as ``name`` into folder ``parent`` (None = root)."""
        ...

    def mkdir(self, *, root: str, parent: str | None, name: str) -> FsEntry:
        """Create folder ``name`` under ``parent`` (None = root). Returns it."""
        ...

    def delete(self, *, root: str, item: str) -> None:
        """Delete file or folder ``item`` within ``root``."""
        ...
