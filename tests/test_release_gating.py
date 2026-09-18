"""Refuse to release while the bundled OpenAPI snapshot is ahead of production.

``sum_cli/data/openapi_snapshot.json`` is normally refreshed from live
``/openapi.json`` (``scripts/refresh_openapi.py``), so it describes what is
deployed. The durable-conversation-queue commands broke that rule on purpose:
``tests/test_openapi_contract.py`` requires every CLI call site to exist in the
snapshot, and the queue contract was not deployed when the commands landed, so
the operations were merged in from sum-api's own generated spec.

That is safe in the repository and unsafe in a release — the published commands
would 404 for every user until sum-api catches up. A README note is not a
control, so this is the control: while anything is listed in
``STAGED_OPERATIONS``, the version cannot move off the last released one, and
releases are version-gated (``.github/workflows/release.yml`` checks the tag
against ``__version__``).

**To release these commands:** confirm the contract is deployed, run
``python scripts/refresh_openapi.py`` against production, empty
``STAGED_OPERATIONS``, then bump ``__version__``.
"""

from __future__ import annotations

from sum_cli import __version__
from sum_cli.openapi_doc import iter_operations, load_spec

# The version this repository has actually published. While operations are
# staged, `__version__` must stay here so no tag can ship them.
LAST_RELEASED_VERSION = "0.1.6"

_CONVERSATION = "/v1/projects/*/conversations/*"

# (method, normalized path) -> the change that has to deploy first.
STAGED_OPERATIONS: dict[tuple[str, str], str] = {
    ("GET", f"{_CONVERSATION}/queue"): "summationai/code#15137 (sum-api queue contract)",
    ("GET", f"{_CONVERSATION}/queue/*"): "summationai/code#15137 (sum-api queue contract)",
    ("DELETE", f"{_CONVERSATION}/queue/*"): "summationai/code#15137 (sum-api queue contract)",
    ("POST", f"{_CONVERSATION}/queue/resume"): "summationai/code#15137 (sum-api queue contract)",
    (
        "POST",
        f"{_CONVERSATION}/messages/*/cancel",
    ): "summationai/code#15137 (sum-api queue contract)",
}


def test_version_is_pinned_while_operations_are_staged() -> None:
    """A version bump is the act of releasing, so block it, not the tag.

    CI runs on the bump's own pull request, so this fails before a tag exists.
    """
    if not STAGED_OPERATIONS:
        return
    pending = "\n".join(
        f"  {method} {path}  (needs {reason})"
        for (method, path), reason in sorted(STAGED_OPERATIONS.items())
    )
    assert __version__ == LAST_RELEASED_VERSION, (
        f"__version__ is {__version__}, but the bundled OpenAPI snapshot is still ahead "
        f"of production:\n{pending}\n"
        "Releasing now would publish commands that 404 for every user. Confirm the "
        "contract is deployed, re-run `python scripts/refresh_openapi.py` against "
        "production, empty STAGED_OPERATIONS in this file, and then bump the version."
    )


def test_staged_operations_still_exist_in_the_snapshot() -> None:
    """Keeps the staged list honest in both directions.

    If a refresh drops one of these, the CLI would reference a route the snapshot
    does not document and `test_openapi_contract` would fail with no explanation
    of why; this names the cause. Once the contract deploys, a production refresh
    keeps them present, and the list is emptied by hand.
    """
    spec_keys = {op.key for op in iter_operations(load_spec())}
    missing = sorted(key for key in STAGED_OPERATIONS if key not in spec_keys)
    assert missing == [], (
        "Staged operations are absent from the bundled snapshot:\n"
        + "\n".join(f"  {method} {path}" for method, path in missing)
        + "\nEither the snapshot was refreshed before the contract deployed, or these "
        "entries are stale and should be removed from STAGED_OPERATIONS."
    )
