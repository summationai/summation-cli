# GENERATED FILE - DO NOT EDIT.
# ruff: noqa: E501
# Source: summationai/Code@84a4a66226e301dcdef3f70bd12c97cc785e3bef:python/libs/sm_verification_contract/custom_test_bundle.py
# Regenerate: python scripts/vendor_verification_contract.py --code-root PATH

"""The custom-test upload bundle contract — the single source shared by the server and the CLI.

One file = one subject type's custom tests for a tenant. Agent-service and sum-api import this
module; summation-cli vendors it mechanically with a source-commit header, so offline validation
matches what the server enforces. ``extra="forbid"`` rejects any field resembling code (e.g.
``verifier_ref``) by construction — custom tests are declarative rubric data only.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sum_cli.verification_contract.rubric_types import RubricTest

CUSTOM_TEST_BUNDLE_VERSION = "custom-test-bundle/v1"
CUSTOM_TEST_SUBJECT_TYPES: tuple[str, ...] = ("html", "document", "deck", "workbook")
CustomTestSubjectType = Literal["html", "document", "deck", "workbook"]
CustomTestRequiredness = Literal["advisory", "blocking"]

# A rubric row id is ``f"{check_ref}:{test_id}:{version_id}"`` persisted to a VARCHAR(255) column.
# User-authored test_ids only ever ride ``custom_rubric`` row ids (overlay adds accumulate into the
# custom_rubric check, never into a committed rubric), whose fixed overhead (check_ref + two ``:`` +
# a ``custom_rubric@<sha256>`` version_id) is ≈ 110 chars — leaving ~145 chars of headroom for
# test_id; cap well under that. Committed rubrics (html/deck/workbook) have larger check_ref
# overhead but their test_ids are code-reviewed constants, bounded by their own tests. ``category``
# is persisted to VerificationV2ResultRow.category (also VARCHAR(255)). Capping both at the upload
# boundary turns a would-be DB-overflow 500 (raised at flush, NOT caught by the IntegrityError
# SAVEPOINT recovery) into a clean 422 at the edge.
MAX_TEST_ID_LEN = 128
MAX_CATEGORY_LEN = 128
# ``name`` and ``pass_criteria`` live in the JSONB ``test`` column (no DB truncation), but they are
# inlined verbatim into every judge prompt for the lifetime of the tenant's test set. Cap them so
# total prompt size is bounded by MAX_BUNDLE_TESTS x these limits rather than unbounded.
MAX_NAME_LEN = 200
# display_name is presentation-only and excluded from semantic digests; it shares the name cap so
# label edits stay bounded without changing verification staleness.
MAX_PASS_CRITERIA_LEN = 2000
# Bound per-verification LLM fan-out: ``run_rubric_judge`` issues one reasoning-model call per
# DISTINCT category. Without a cap an uploaded bundle with hundreds of unique categories would burst
# the provider for a single verification (rate-limit / timeout / cost). Cap both the test count and
# the distinct-category count at the bundle boundary so the CLI's offline lint and the upload route
# reject oversized bundles identically.
MAX_BUNDLE_TESTS = 200
MAX_BUNDLE_CATEGORIES = 50
# User-authored ``test_id`` values become first-class ``test_ref`` identities on result rows. Keep
# them disjoint from system-owned refs so downstream code can safely key by ``test_ref`` without
# conflating a custom rubric test with a built-in or wrapper identity.
RESERVED_CUSTOM_TEST_REFS = frozenset(
    {
        "citation_accuracy",
        "claim_accuracy",
        "query_accuracy",
        "traceability",
        "quality_review",
        "html_rubric",
        "deck_rubric",
        "workbook_rubric",
        "workbook_quality",
        "custom_rubric",
        # Verification Hub category aliases map to built-in check refs on the frontend. Reserve every
        # non-verifier key of V2_CATEGORY_MAP (derive-hub-categories.ts) so user-authored test_refs
        # cannot collide with current or future fallback routing.
        "citations",
        "claim_logic",
        "claims",
        "queries",
        "query_definitions",
        "traces",
    }
)


class CustomTestBundleEntry(BaseModel):
    """One test in an upload bundle — exactly the engine RubricTest fields, nothing else."""

    model_config = ConfigDict(extra="forbid")

    test_id: str = Field(min_length=1, max_length=MAX_TEST_ID_LEN)
    category: str = Field(min_length=1, max_length=MAX_CATEGORY_LEN)
    name: str = Field(min_length=1, max_length=MAX_NAME_LEN)
    display_name: str | None = Field(default=None, max_length=MAX_NAME_LEN)
    pass_criteria: str = Field(min_length=1, max_length=MAX_PASS_CRITERIA_LEN)
    requiredness: CustomTestRequiredness = "advisory"
    render_dependent: bool = False

    @field_validator("test_id")
    @classmethod
    def _slug_test_id(cls, value: str) -> str:
        if not value.replace("_", "").isalnum() or value != value.lower():
            raise ValueError("test_id must be a lowercase snake_case slug")
        if value in RESERVED_CUSTOM_TEST_REFS:
            raise ValueError("test_id must not collide with a reserved verification test ref")
        return value

    @field_validator("category", "name", "pass_criteria")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    def to_rubric_test(self) -> RubricTest:
        return RubricTest(
            test_id=self.test_id,
            category=self.category,
            name=self.name,
            display_name=self.display_name,
            pass_criteria=self.pass_criteria,
            render_dependent=self.render_dependent,
            requiredness=self.requiredness,
        )


class CustomTestBundle(BaseModel):
    """The uploaded bundle: one subject type's custom tests for a tenant."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["custom-test-bundle/v1"]
    subject_type: CustomTestSubjectType
    tests: list[CustomTestBundleEntry] = Field(min_length=1, max_length=MAX_BUNDLE_TESTS)

    @model_validator(mode="after")
    def _unique_test_ids(self) -> "CustomTestBundle":
        ids = [test.test_id for test in self.tests]
        if len(ids) != len(set(ids)):
            raise ValueError("test_id values must be unique within a bundle")
        return self

    @model_validator(mode="after")
    def _bounded_categories(self) -> "CustomTestBundle":
        distinct = {test.category for test in self.tests}
        if len(distinct) > MAX_BUNDLE_CATEGORIES:
            raise ValueError(
                f"bundle has {len(distinct)} distinct categories; the maximum is {MAX_BUNDLE_CATEGORIES} "
                "(each category is one reasoning-model call per verification)"
            )
        return self
