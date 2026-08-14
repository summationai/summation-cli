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
FETCH_TIMEOUT = 0.4
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
    # Click sets resilient_parsing while generating --help. Do not scan argv:
    # `-m --help` is a legitimate option value (see chats/reports --message).
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


def _uv_missing(message: str) -> NoReturn:
    emit_error(
        err(
            "UV_NOT_FOUND",
            message,
            "Install uv, or re-run the bootstrap installer.",
            next_actions=[action("Bootstrap install", _BOOTSTRAP)],
        )
    )


def run_upgrade(*, current: str = __version__) -> None:
    """Install the latest PyPI release, including over an exact-version pin.

    Re-checks PyPI (does not use the daily cache) and skips uv when ``current``
    is already the latest known release.
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
