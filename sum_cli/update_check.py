"""Non-blocking PyPI version check and `sumcli update`.

The check never writes to stdout (JSON envelopes stay parseable). Failures are
swallowed. Results are cached next to the config file so a cold PyPI lookup
happens at most once per TTL (shorter on fetch failure).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

import click
import httpx

from sum_cli import __version__, debug_log
from sum_cli.config_store import config_path
from sum_cli.output import action, emit, emit_error, err, ok

PACKAGE = "summation-cli"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CACHE_NAME = "update-check.json"
TTL_SECONDS = 24 * 60 * 60
FAILURE_TTL_SECONDS = 15 * 60
FETCH_TIMEOUT = 1.0
UV_INSTALL = ["tool", "install", "--force", f"{PACKAGE}@latest"]
_TRUTHY = frozenset({"1", "true", "yes"})
_BOOTSTRAP = "curl -fsSL https://install.summation.com/sumcli | sh"


def cache_path() -> Path:
    return config_path().parent / CACHE_NAME


def _env_disabled() -> bool:
    return os.environ.get("SUMCLI_NO_UPDATE_CHECK", "").strip().lower() in _TRUTHY


_ran = False


def reset_state() -> None:
    """Test helper: allow another check in this process."""
    global _ran
    _ran = False


def _skip_this_invocation() -> bool:
    if _env_disabled():
        return True
    # Click sets resilient_parsing during shell completion, not --help. An eager
    # --help exits before the root callback; `sumcli <cmd> --help` still runs
    # the check, which is fine. Completion must never touch the network.
    # Do not scan argv: `-m --help` is a legitimate option value
    # (see chats/reports --message).
    ctx = click.get_current_context(silent=True)
    return bool(ctx is not None and getattr(ctx, "resilient_parsing", False))


def _parse_version(value: str) -> tuple[int, ...] | None:
    parts = value.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _is_behind(current: str, latest: str) -> bool:
    cur = _parse_version(current)
    lat = _parse_version(latest)
    if cur is None or lat is None:
        return False
    return cur < lat


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "latest" not in data:
        return None
    latest = data["latest"]
    if latest is not None and not isinstance(latest, str):
        return None
    checked_at = data.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return None
    ok_flag = data.get("ok")
    if not isinstance(ok_flag, bool):
        ok_flag = latest is not None
    return {"latest": latest, "checked_at": float(checked_at), "ok": ok_flag}


def _write_cache(
    path: Path,
    latest: str | None,
    checked_at: float | None = None,
    *,
    ok: bool = True,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        checked = time.time() if checked_at is None else checked_at
        payload = json.dumps({"latest": latest, "checked_at": checked, "ok": ok})
        fd, tmp_name = tempfile.mkstemp(
            prefix="update-check.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    except OSError:
        pass


def _fetch_latest() -> str | None:
    try:
        resp = httpx.get(
            PYPI_JSON_URL,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": f"sumcli/{__version__}"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        payload = resp.json()
        info = payload.get("info") if isinstance(payload, dict) else None
        latest = info.get("version") if isinstance(info, dict) else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(latest, str) or not latest:
        return None
    return latest


def resolve_latest(*, now: float | None = None) -> str | None:
    """Return the latest PyPI version, using cache when it is still fresh."""
    path = cache_path()
    cached = _read_cache(path)
    stamp = time.time() if now is None else now
    if cached is not None:
        ttl = TTL_SECONDS if cached["ok"] else FAILURE_TTL_SECONDS
        if stamp - cached["checked_at"] < ttl:
            return cached["latest"]
    latest = _fetch_latest()
    if latest is None:
        previous = cached["latest"] if cached is not None else None
        _write_cache(path, previous, stamp, ok=False)
        return previous
    _write_cache(path, latest, stamp, ok=True)
    return latest


def warn_if_outdated(*, current: str = __version__) -> None:
    """Print a stderr notice when `current` is behind PyPI. Never raises."""
    global _ran
    if _ran or _skip_this_invocation():
        return
    _ran = True
    try:
        latest = resolve_latest()
        if latest is None or not _is_behind(current, latest):
            return
        print(
            f"sumcli: {current} is behind {latest}. Run: sumcli update",
            file=sys.stderr,
        )
    except Exception as exc:
        debug_log.debug("update check skipped: %s", exc)


def _uv_tool_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    if explicit := os.environ.get("UV_TOOL_DIR"):
        candidates.append(Path(explicit).expanduser())
    if xdg := os.environ.get("XDG_DATA_HOME"):
        candidates.append(Path(xdg) / "uv" / "tools")
    home = Path.home()
    candidates.append(home / ".local" / "share" / "uv" / "tools")
    if appdata := os.environ.get("APPDATA"):
        candidates.append(Path(appdata) / "uv" / "tools")
    if local := os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(local) / "uv" / "tools")
    candidates.append(home / "Library" / "Application Support" / "uv" / "tools")
    return candidates


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _looks_like_uv_tools_path(path: Path) -> bool:
    parts = [part.lower() for part in str(path).replace("\\", "/").split("/") if part]
    return any(
        part == "uv" and parts[index + 1] == "tools"
        for index, part in enumerate(parts[:-1])
    )


def _is_uv_managed(*, prefix: str | None = None, executable: str | None = None) -> bool:
    """True when this process is running from a `uv tool` environment."""
    roots = (
        Path(sys.prefix if prefix is None else prefix),
        Path(sys.executable if executable is None else executable),
    )
    tool_dirs = _uv_tool_dir_candidates()
    for root in roots:
        if _looks_like_uv_tools_path(root):
            return True
        if any(_is_under(root, tool_dir) for tool_dir in tool_dirs):
            return True
    return False


def _uv_missing(message: str) -> NoReturn:
    emit_error(
        err(
            "UV_NOT_FOUND",
            message,
            "Install uv, or re-run the bootstrap installer.",
            next_actions=[action("Bootstrap install", _BOOTSTRAP)],
        )
    )


def _not_uv_managed() -> NoReturn:
    emit_error(
        err(
            "NOT_UV_MANAGED",
            "This sumcli was not installed with uv, so sumcli update would "
            "create a second copy instead of upgrading the running binary.",
            "Upgrade with the same installer you used, or switch to a uv-managed install.",
            next_actions=[
                action("Install with uv", f"uv {' '.join(UV_INSTALL)}"),
                action("Upgrade with pip", "pip install --upgrade summation-cli"),
                action("Upgrade with pipx", "pipx upgrade summation-cli"),
                action("Bootstrap install", _BOOTSTRAP),
            ],
        )
    )


def run_upgrade(*, current: str = __version__) -> None:
    """Install the latest PyPI release, including over an exact-version pin.

    Re-checks PyPI (does not use the daily cache) and skips uv when ``current``
    is already the latest known release. Refuses to run ``uv tool install``
    unless this process is a uv-managed tool, so pip/pipx/brew installs are
    not shadowed by a second copy.
    """
    latest = _fetch_latest()
    if latest is not None and not _is_behind(current, latest):
        _write_cache(cache_path(), latest, ok=True)
        emit(
            ok(
                {
                    "current_version": current,
                    "latest": latest,
                    "updated": False,
                    "note": "Already the latest PyPI release.",
                },
                next_actions=[action("Show version", "sumcli --version")],
            )
        )
        return
    if not _is_uv_managed():
        _not_uv_managed()
    uv = shutil.which("uv")
    if uv is None:
        _uv_missing("uv is not on PATH, so sumcli cannot upgrade itself.")
    argv = [uv, *UV_INSTALL]
    try:
        proc = subprocess.run(
            argv,
            stdout=sys.stderr,
            stderr=sys.stderr,
            check=False,
        )
    except OSError as exc:
        _uv_missing(f"Could not run uv: {exc}")
    if proc.returncode != 0:
        cmd = " ".join(argv[1:])
        emit_error(
            err(
                "UPDATE_FAILED",
                f"uv {cmd} exited {proc.returncode}.",
                "Install via uv tool install summation-cli, then retry sumcli update.",
                next_actions=[
                    action("Install latest", f"uv {' '.join(UV_INSTALL)}"),
                ],
            )
        )
    try:
        cache_path().unlink(missing_ok=True)
    except OSError:
        pass
    emit(
        ok(
            {
                "previous_version": current,
                "latest": latest,
                "package": PACKAGE,
                "updated": True,
                "note": "Restart the shell command to pick up the new binary.",
            },
            next_actions=[action("Show version", "sumcli --version")],
        )
    )
