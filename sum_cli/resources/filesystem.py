"""`sumcli filesystem ...` — provider-agnostic external storage.

Reads/writes files on external providers (SharePoint first; S3/Box to follow)
via the :mod:`sum_cli.filesystem` protocol. These commands do NOT use the
sum-api client — each provider talks to its own host with its own credentials.

    sumcli filesystem roots    --provider sharepoint
    sumcli filesystem list     --provider sharepoint [--root <id>] [--path <folder-id>]
    sumcli filesystem download --provider sharepoint --root <root> --item <id> -o out
    sumcli filesystem upload   --provider sharepoint --root <root> --file f [--path <id>]
    sumcli filesystem upload   --provider sharepoint --file f --overwrite --confirm
    sumcli filesystem delete   --provider sharepoint --root <root> --item <id> --confirm

Roots and entries are addressed by backend-native ids (not paths): run `roots`
to discover root ids, then `list` to discover folder/file ids. Set
SHAREPOINT_ROOT / SHAREPOINT_PATH (or pass --root / --path) to avoid repeating ids.
Persist defaults with ``sumcli filesystem set-defaults`` or import from a
``.env`` file with ``sumcli filesystem import-env``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from sum_cli.commands import require_confirm
from sum_cli.env_import import EnvImportError
from sum_cli.filesystem import PROVIDERS, FileSystemError, UnknownProvider, get_filesystem
from sum_cli.filesystem.base import FsEntry, FsListResult
from sum_cli.filesystem.config_defaults import (
    effective_filesystem_defaults,
    resolve_path,
    resolve_root,
    set_filesystem_defaults,
)
from sum_cli.filesystem.import_env import import_sharepoint_from_env_file
from sum_cli.output import (
    DEFAULT_LIST_COUNT,
    action,
    emit,
    emit_error,
    err,
    ok,
    param,
    truncate_list,
)
from sum_cli.tempfiles import write_stream, write_temp_stream

app = typer.Typer(no_args_is_help=True, help="External filesystem providers (sharepoint, ...).")

# The backend needs a concrete page size, so we can't defer to truncate_list's
# own default the way API-backed resources do; mirror it from one source instead.
_DEFAULT_LIST_COUNT = DEFAULT_LIST_COUNT

ProviderOption = Annotated[
    str | None,
    typer.Option("--provider", help=f"Provider: {', '.join(PROVIDERS)}."),
]

_ROOTS = action(
    "List provider roots",
    "sumcli filesystem roots --provider <provider>",
    params={"provider": param("Provider", enum=PROVIDERS)},
)


def _require_provider(provider: str | None) -> str:
    """Validate ``--provider`` against the registry, emitting the standard error."""
    if not provider or provider not in PROVIDERS:
        e = UnknownProvider(provider)
        emit_error(err(e.code, e.message, e.fix, next_actions=[_ROOTS]))
    return provider


def _fs(provider: str | None):
    try:
        return get_filesystem(provider)
    except FileSystemError as e:
        emit_error(err(e.code, e.message, e.fix, next_actions=[_ROOTS]))


def _guard(ctx: typer.Context, fs, fn):
    """Run a backend call, translating FileSystemError to an error envelope."""
    try:
        return fn()
    except FileSystemError as e:
        emit_error(err(e.code, e.message, e.fix, next_actions=[_ROOTS]))
    finally:
        close = getattr(fs, "close", None)
        if callable(close):
            close()


def _existing_file(fs, *, root: str, parent: str | None, name: str) -> FsEntry | None:
    """Return a same-named file in ``parent``, if one is visible in the first list page."""
    result: FsListResult = fs.list(root=root, path=parent, limit=_DEFAULT_LIST_COUNT)
    for entry in result.entries:
        if entry.kind == "file" and entry.name == name:
            return entry
    return None


@app.command("roots")
def roots(ctx: typer.Context, provider: ProviderOption = None) -> None:
    fs = _fs(provider)
    items = _guard(ctx, fs, fs.roots)
    emit(ok({"provider": fs.provider, "roots": [r.to_dict() for r in items], "count": len(items)}))


@app.command("import-env")
def import_env(
    env_file: Annotated[Path, typer.Argument(help="Skill-style env file (.env).")],
    provider: ProviderOption = None,
) -> None:
    _require_provider(provider)
    try:
        result = import_sharepoint_from_env_file(env_file)
    except FileNotFoundError:
        emit_error(
            err(
                "FILE_NOT_FOUND",
                f"Env file not found: {env_file}",
                "Pass a path to your .env or skill-style env file.",
            )
        )
    except EnvImportError as exc:
        emit_error(err(exc.code, exc.message, exc.hint))
    except ValueError as exc:
        emit_error(
            err(
                "IMPORT_EMPTY",
                str(exc),
                "Include SHAREPOINT_* keys in the env file.",
            )
        )
    emit(
        ok(
            {
                "provider": "sharepoint",
                "config_path": str(result.config_path),
                "imported_from": str(result.imported_from),
                "credentials": result.credentials,
                "defaults": result.defaults,
            },
            next_actions=[
                action("List files", "sumcli filesystem list --provider sharepoint"),
                action("Show roots", "sumcli filesystem roots --provider sharepoint"),
            ],
        )
    )


@app.command("set-defaults")
def set_defaults(
    provider: ProviderOption = None,
    root: Annotated[str | None, typer.Option("--root", help="Drive id to persist.")] = None,
    path: Annotated[
        str | None,
        typer.Option("--path", help="Folder id to persist."),
    ] = None,
) -> None:
    if root is None and path is None:
        emit_error(
            err(
                "MISSING_ARGS",
                "Pass --root and/or --path.",
                "Run `sumcli filesystem roots` and `list` to discover ids.",
                next_actions=[_ROOTS],
            )
        )
    provider = _require_provider(provider)
    config_file = set_filesystem_defaults(provider=provider, root=root, path=path)
    effective = effective_filesystem_defaults(provider=provider)
    persisted: dict[str, str] = {}
    if root is not None:
        persisted["root"] = root
    if path is not None:
        persisted["path"] = path
    emit(
        ok(
            {
                "provider": provider,
                "config_path": str(config_file),
                "persisted": persisted,
                "defaults": effective,
            },
            next_actions=[
                action(
                    "List with defaults",
                    f"sumcli filesystem list --provider {provider}",
                ),
            ],
        )
    )


@app.command("list")
def list_entries(
    ctx: typer.Context,
    provider: ProviderOption = None,
    root: Annotated[
        str | None,
        typer.Option("--root", help="Drive id; default SHAREPOINT_ROOT or sole site drive."),
    ] = None,
    path: Annotated[
        str | None,
        typer.Option("--path", help="Folder id; default SHAREPOINT_PATH; omit for drive root."),
    ] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
) -> None:
    fs = _fs(provider)
    cap = count if count is not None else _DEFAULT_LIST_COUNT

    def _run() -> tuple[object, str, str | None]:
        resolved_root = resolve_root(fs, root)
        resolved_path = resolve_path(fs, path)
        result = fs.list(root=resolved_root, path=resolved_path, limit=cap)
        return result, resolved_root, resolved_path

    list_result, resolved_root, resolved_path = _guard(ctx, fs, _run)
    listed = truncate_list([e.to_dict() for e in list_result.entries], count=count)
    if list_result.truncated:
        listed["truncated"] = True
    emit(
        ok(
            {
                "provider": fs.provider,
                "root": resolved_root,
                "path": resolved_path,
                "entries": listed["items"],
                **{k: v for k, v in listed.items() if k != "items"},
            }
        )
    )


@app.command("download")
def download(
    ctx: typer.Context,
    provider: ProviderOption = None,
    root: Annotated[
        str | None,
        typer.Option("--root", help="Drive id; default SHAREPOINT_ROOT or sole site drive."),
    ] = None,
    item: Annotated[str | None, typer.Option("--item", help="File id (run `list`).")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    fs = _fs(provider)
    if not item:
        emit_error(
            err(
                "MISSING_ARGS",
                "--item is required.",
                "Run `sumcli filesystem list` to discover file ids.",
                next_actions=[_ROOTS],
            )
        )

    def _run() -> tuple[Path, int, str]:
        resolved_root = resolve_root(fs, root)
        chunks = fs.download(root=resolved_root, item=item)
        if output:
            total = write_stream(dest=output, chunks=chunks)
            dest = output
        else:
            dest, total = write_temp_stream(prefix="sumcli-fs-", chunks=chunks)
        return dest, total, resolved_root

    dest, total, resolved_root = _guard(ctx, fs, _run)
    emit(
        ok(
            {
                "provider": fs.provider,
                "root": resolved_root,
                "item": item,
                "path": str(dest),
                "bytes": total,
            }
        )
    )


@app.command("upload")
def upload(
    ctx: typer.Context,
    file: Annotated[Path, typer.Option("--file", help="Local file to upload.")],
    provider: ProviderOption = None,
    root: Annotated[
        str | None,
        typer.Option("--root", help="Drive id; default SHAREPOINT_ROOT or sole site drive."),
    ] = None,
    path: Annotated[
        str | None,
        typer.Option("--path", help="Destination folder id; default SHAREPOINT_PATH."),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing file with the same name."),
    ] = False,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    fs = _fs(provider)
    if not file.exists():
        emit_error(err("FILE_NOT_FOUND", f"Local file not found: {file}", "Check the --file path."))
    data = file.read_bytes()

    def _run() -> tuple[object, str, str | None, FsEntry | None]:
        resolved_root = resolve_root(fs, root)
        resolved_path = resolve_path(fs, path)
        existing = _existing_file(fs, root=resolved_root, parent=resolved_path, name=file.name)
        if existing and not overwrite:
            raise FileSystemError(
                "FILE_EXISTS",
                f"A file named {file.name!r} already exists (id {existing.id}).",
                "Pass --overwrite --confirm to replace it, or upload under a different name.",
            )
        if existing and overwrite:
            require_confirm(confirm, action_name="filesystem upload overwrite")
        entry = fs.upload(root=resolved_root, parent=resolved_path, name=file.name, data=data)
        return entry, resolved_root, resolved_path, existing

    entry, resolved_root, resolved_path, existing = _guard(ctx, fs, _run)
    result: dict = {
        "provider": fs.provider,
        "root": resolved_root,
        "path": resolved_path,
        "entry": entry.to_dict(),
    }
    if existing is not None:
        result["overwritten"] = True
        result["replaced_item"] = existing.id
    emit(ok(result))


@app.command("mkdir")
def mkdir(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="New folder name.")],
    provider: ProviderOption = None,
    root: Annotated[
        str | None,
        typer.Option("--root", help="Drive id; default SHAREPOINT_ROOT or sole site drive."),
    ] = None,
    path: Annotated[
        str | None,
        typer.Option("--path", help="Parent folder id; default SHAREPOINT_PATH."),
    ] = None,
) -> None:
    fs = _fs(provider)

    def _run() -> tuple[object, str, str | None]:
        resolved_root = resolve_root(fs, root)
        resolved_path = resolve_path(fs, path)
        entry = fs.mkdir(root=resolved_root, parent=resolved_path, name=name)
        return entry, resolved_root, resolved_path

    entry, resolved_root, resolved_path = _guard(ctx, fs, _run)
    emit(
        ok(
            {
                "provider": fs.provider,
                "root": resolved_root,
                "path": resolved_path,
                "entry": entry.to_dict(),
            }
        )
    )


@app.command("delete")
def delete(
    ctx: typer.Context,
    provider: ProviderOption = None,
    root: Annotated[
        str | None,
        typer.Option("--root", help="Drive id; default SHAREPOINT_ROOT or sole site drive."),
    ] = None,
    item: Annotated[str | None, typer.Option("--item")] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    require_confirm(confirm, action_name="filesystem delete")
    fs = _fs(provider)
    if not item:
        emit_error(
            err(
                "MISSING_ARGS",
                "--item is required.",
                "Run `sumcli filesystem list` to discover ids.",
                next_actions=[_ROOTS],
            )
        )

    def _run() -> str:
        resolved_root = resolve_root(fs, root)
        fs.delete(root=resolved_root, item=item)
        return resolved_root

    resolved_root = _guard(ctx, fs, _run)
    emit(ok({"provider": fs.provider, "root": resolved_root, "deleted": item}))
