# GENERATED FILE - DO NOT EDIT.
# ruff: noqa: E501
# Source: summationai/Code@84a4a66226e301dcdef3f70bd12c97cc785e3bef:python/libs/sm_verification_contract/rubric_types.py
# Regenerate: python scripts/vendor_verification_contract.py --code-root PATH

"""Shared, subject-agnostic rubric test type + digest/grouping helpers.

This lightweight package is the canonical home for the declarative ``RubricTest`` shape and the
content-digest mechanism behind every rubric test set (HTML today, deck next, and custom
overlay tests). It is imported by ``html_rubric_data``, ``deck_rubric_data``,
``verification_v2`` and the shared judge, so it MUST stay stdlib-only (no engine imports) to
avoid an import cycle — the same constraint ``html_rubric_data`` already documents.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

VerificationV2Requiredness = Literal["blocking", "advisory"]

# How the shared row-builder treats a ``render_dependent`` test when the host lacks the
# ``vision`` capability. HTML keeps shipped best-effort behavior; deck (and any subject that
# inherits its policy) skips render-dependent rows cleanly until ``vision`` lands.
RenderDependentPolicy = Literal["BEST_EFFORT", "SKIP_WITHOUT_VISION"]


@dataclass(frozen=True)
class RubricTest:
    """One named rubric test, judged against a subject's text source by the shared LLM judge."""

    test_id: str  # stable snake_case slug; feeds the per-test result row_id
    category: str  # UI grouping bucket
    name: str  # human title shown in the UI
    pass_criteria: str  # natural-language PASS/FAIL rule, verbatim from the product rubric
    # Pure-render checks a text LLM cannot judge reliably from source (truncation/overlap,
    # color, type hierarchy). Per the rubric's RenderDependentPolicy, either sent best-effort
    # (HTML) or skipped until a ``vision`` host capability exists (deck/custom).
    render_dependent: bool = False
    # Per-test badge impact. Advisory to start (failures are informational); flip specific
    # tests to "blocking" in the data module (a data edit, not a logic change) once trusted.
    requiredness: VerificationV2Requiredness = "advisory"
    display_name: str | None = None  # optional presentation label; excluded from semantic digests
    # Structured mode applicability (e.g. workbook build/augment/refresh). ``None`` means the test
    # applies in every mode. When set, the row builder judges the test only when the subject's mode
    # is one of these values; outside them (or when the mode is unknown) the row is emitted as an
    # ADVISORY deterministic skip — a blocking contract is scoped to its declared modes, so a
    # mode-inapplicable row must never fail the badge as ``blocking_skipped``. Included in the
    # semantic digest only when set, so pre-existing rubrics (html/deck) keep their digests.
    modes: tuple[str, ...] | None = None

    @property
    def effective_display_name(self) -> str:
        """User-facing label for this rubric test, falling back to the legacy name."""
        return self.display_name or self.name


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _rubric_test_digest_payload(test: RubricTest) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": test.test_id,
        "category": test.category,
        "name": test.name,
        "pass_criteria": test.pass_criteria,
        "render_dependent": test.render_dependent,
        "requiredness": test.requiredness,
    }
    # Only present when set: keeps the digests of rubrics that predate the field (html/deck)
    # byte-identical, so their test-set versions do not bump and no persisted run flips stale.
    if test.modes is not None:
        payload["modes"] = list(test.modes)
    return payload


def _rubric_digest_hex(tests: tuple[RubricTest, ...]) -> str:
    """Raw sha256 hex over the rubric content; the single canonical content digest."""
    payload = {"tests": [_rubric_test_digest_payload(test) for test in tests]}
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def rubric_snapshot_digest(tests: tuple[RubricTest, ...], version_prefix: str) -> str:
    """Versioned content digest for a rubric: ``f"{version_prefix}@{hex}"``.

    Editing any criterion/requiredness/render-dependence changes the hex, so the test-set
    version bumps automatically (staleness falls out for free).
    """
    return f"{version_prefix}@{_rubric_digest_hex(tests)}"


def tests_by_category(tests: tuple[RubricTest, ...]) -> dict[str, tuple[RubricTest, ...]]:
    """Group rubric tests by category, preserving first-appearance (declaration) order."""
    grouped: dict[str, list[RubricTest]] = {}
    for test in tests:
        grouped.setdefault(test.category, []).append(test)
    return {category: tuple(category_tests) for category, category_tests in grouped.items()}
