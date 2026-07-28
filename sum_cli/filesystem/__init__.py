"""Provider-agnostic external filesystem access for sumcli.

The :mod:`sum_cli.filesystem` package defines a small ``FileSystem`` protocol
(:mod:`sum_cli.filesystem.base`) and a registry of concrete backends
(:mod:`sum_cli.filesystem.registry`). SharePoint is the first implementation;
S3 and Box are intended to slot in behind the same protocol.

Unlike the rest of sumcli, these backends do NOT go through
:class:`sum_cli.client.Client` — that client is bound to sum-api's base URL and
bearer token. External storage providers talk to their own hosts with their own
auth, so each backend owns its HTTP client and credential resolution.
"""

from __future__ import annotations

from sum_cli.filesystem.base import FileSystem, FsEntry, FsRoot
from sum_cli.filesystem.registry import (
    PROVIDERS,
    FileSystemError,
    UnknownProvider,
    get_filesystem,
)

__all__ = [
    "FileSystem",
    "FsEntry",
    "FsRoot",
    "PROVIDERS",
    "FileSystemError",
    "UnknownProvider",
    "get_filesystem",
]
