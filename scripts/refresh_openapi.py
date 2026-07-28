#!/usr/bin/env python3
"""Refresh or verify the bundled OpenAPI snapshot used by sumcli.

The snapshot at ``sum_cli/data/openapi_snapshot.json`` is shipped in the wheel
and lets contract tests run offline. Re-run when sum-api ships new routes:

    python scripts/refresh_openapi.py
    python scripts/refresh_openapi.py --base-url https://api.summation.com

Verify the committed snapshot matches production (nightly automation or manual
pre-release; per-PR CI gates on offline contract tests only):

    python scripts/refresh_openapi.py --check

Then run the contract tests:

    python -m pytest tests/test_openapi_contract.py -q
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://api.summation.com"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "sum_cli" / "data" / "openapi_snapshot.json"


def fetch_live_spec(base_url: str, *, attempts: int = 3) -> dict:
    """Fetch live ``/openapi.json``, retrying transient failures.

    A prod blip or mid-deploy state should not fail the nightly check, so retry
    a few times with linear backoff before giving up.
    """
    url = f"{base_url.rstrip('/')}/openapi.json"
    last_exc: httpx.HTTPError | json.JSONDecodeError | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.get(url, timeout=30.0)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(attempt)
            continue
        if not isinstance(body, dict):
            raise SystemExit(f"Expected JSON object from {url}, got {type(body).__name__}")
        return body
    raise SystemExit(f"Failed to fetch {url} after {attempts} attempts: {last_exc}")


def canonical_spec_text(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("SUM_API_BASE_URL", DEFAULT_BASE_URL),
        help="sum-api host to fetch /openapi.json from (default: %(default)s).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SNAPSHOT_PATH,
        help="Snapshot path to write (default: %(default)s).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the bundled snapshot differs from live /openapi.json (no write).",
    )
    args = parser.parse_args()

    live = fetch_live_spec(args.base_url)
    if args.check:
        if not args.out.is_file():
            print(f"Missing bundled snapshot: {args.out}", file=sys.stderr)
            return 1
        local = json.loads(args.out.read_text(encoding="utf-8"))
        if canonical_spec_text(local) != canonical_spec_text(live):
            print(
                f"Bundled snapshot differs from {args.base_url.rstrip('/')}/openapi.json.\n"
                "Run: bazel run //python/packages/sum_cli:refresh_openapi",
                file=sys.stderr,
            )
            return 1
        paths = live.get("paths", {})
        print(f"Snapshot matches live spec ({len(paths)} paths).")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical_spec_text(live), encoding="utf-8")
    paths = live.get("paths", {})
    url = f"{args.base_url.rstrip('/')}/openapi.json"
    print(f"Wrote {args.out} ({len(paths)} paths) from {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
