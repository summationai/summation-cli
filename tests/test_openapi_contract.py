"""OpenAPI contract drift guard for sumcli."""

from __future__ import annotations

from sum_cli.openapi_doc import (
    UNCOVERED_OPERATIONS_ALLOWLIST,
    allowlisted_operations_now_covered,
    cli_call_sites_missing_confirm,
    cli_paths_missing_from_spec,
    load_spec,
    uncovered_spec_operations,
)


def test_cli_call_sites_exist_in_openapi_snapshot() -> None:
    spec = load_spec()
    missing = cli_paths_missing_from_spec(spec)
    assert missing == [], (
        "CLI references routes missing from the vendored OpenAPI snapshot:\n"
        + "\n".join(f"  {site.method} {site.path} ({site.source})" for site in missing)
    )


def test_destructive_delete_call_sites_send_confirm() -> None:
    spec = load_spec()
    missing = cli_call_sites_missing_confirm(spec)
    assert missing == [], (
        "Destructive DELETE call sites must send confirm=true (see api_confirm_params):\n"
        + "\n".join(f"  {site.method} {site.path} ({site.source})" for site in missing)
    )


def test_uncovered_openapi_operations_are_allowlisted() -> None:
    spec = load_spec()
    uncovered = uncovered_spec_operations(spec)
    assert uncovered == [], (
        "OpenAPI operations have no sumcli coverage and are not allow-listed:\n"
        + "\n".join(
            f"  {op.method} {op.path}  # add to UNCOVERED_OPERATIONS_ALLOWLIST with a reason"
            for op in uncovered
        )
    )


def test_allowlist_entries_reference_real_spec_operations() -> None:
    spec = load_spec()
    from sum_cli.openapi_doc import iter_operations

    spec_keys = {op.key for op in iter_operations(spec)}
    stale = sorted(key for key in UNCOVERED_OPERATIONS_ALLOWLIST if key not in spec_keys)
    assert stale == [], "Allow-list entries no longer exist in the OpenAPI snapshot:\n" + "\n".join(
        f"  {method} {path}" for method, path in stale
    )


def test_allowlist_has_no_entries_the_cli_now_covers() -> None:
    """An entry claiming a route is unexposed must go when a command starts calling it."""
    spec = load_spec()
    stale = allowlisted_operations_now_covered(spec)
    assert stale == [], (
        "Allow-list entries name operations sumcli now calls; delete them:\n"
        + "\n".join(f"  {op.method} {op.path}" for op in stale)
    )
