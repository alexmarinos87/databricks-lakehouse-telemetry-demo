#!/usr/bin/env python3
"""Verify and independently re-review a same-run bundle plan artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from review_databricks_plan import (
    ReviewError as PlanReviewError,
    load_policy as load_plan_review_policy,
    parse_plan as parse_plan_review,
    render_summary as render_plan_review_summary,
    review_plan as recompute_plan_review,
)


PLAN_FILE = "bundle-plan.json"
EVIDENCE_FILE = "evidence.json"
SUMMARY_FILE = "summary.md"
VALIDATION_FILE = "bundle-validate.txt"
PLAN_REVIEW_FILE = "databricks-plan-review.json"
PLAN_REVIEW_SUMMARY_FILE = "databricks-plan-review.md"
PLAN_WARNING_FILE = "bundle-plan-warnings.txt"
VALIDATION_WARNING_FILE = "bundle-validate-warnings.txt"
PLAN_REVIEW_POLICY_PATH = "governance/databricks_plan_review_policy.json"
PLAN_REVIEW_POLICY = Path(__file__).resolve().parents[1] / PLAN_REVIEW_POLICY_PATH
BUNDLE_PLAN_SHAPE_ERROR = "bundle_plan_unexpected_shape"
REQUIRED_FILES = {
    EVIDENCE_FILE,
    SUMMARY_FILE,
    VALIDATION_FILE,
    PLAN_FILE,
    PLAN_REVIEW_FILE,
    PLAN_REVIEW_SUMMARY_FILE,
}
OPTIONAL_FILES = {PLAN_WARNING_FILE, VALIDATION_WARNING_FILE}
ALLOWED_FILES = REQUIRED_FILES | OPTIONAL_FILES
MAX_EVIDENCE_BYTES = 1_000_000
MAX_REVIEW_BYTES = 2_000_000
MAX_PLAN_BYTES = 4_000_000
MAX_TEXT_BYTES = 1_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]{0,31}\Z")
REVIEW_KEYS = {
    "schema_version",
    "status",
    "generated_at_utc",
    "target",
    "source_commit",
    "plan_sha256",
    "plan_bytes",
    "plan_version",
    "cli_version",
    "lineage_fingerprint",
    "serial",
    "not_selected",
    "resource_count",
    "resource_actions",
    "permission_sensitive_resources",
    "resources",
    "findings",
}
REVIEW_METADATA_KEYS = {
    "status",
    "schema_version",
    "policy_file",
    "json_file",
    "markdown_file",
    "plan_sha256",
    "resource_count",
    "finding_count",
}


class ArtifactError(RuntimeError):
    """Stable fail-closed error category safe to expose in workflow logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class ExpectedProvenance:
    target: str
    repository: str
    ref: str
    commit: str
    run_id: str
    run_attempt: str


def _expect(condition: bool, category: str) -> None:
    if not condition:
        raise ArtifactError(category)


def _read_regular_file(path: Path, *, maximum_bytes: int, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise ArtifactError(category)
        size = path.stat().st_size
        if size < 0 or size > maximum_bytes:
            raise ArtifactError(f"{category}_size_invalid")
        return path.read_bytes()
    except ArtifactError:
        raise
    except OSError:
        raise ArtifactError(f"{category}_unreadable") from None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_json_object(value: bytes, *, category: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ArtifactError(f"{category}_invalid_json") from None
    if not isinstance(parsed, dict):
        if category == "bundle_plan":
            raise ArtifactError(BUNDLE_PLAN_SHAPE_ERROR)
        raise ArtifactError(f"{category}_unexpected_shape")
    return parsed


def _expect_exact_keys(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ArtifactError(category)
    return value


def _expect_string(value: Any, *, category: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ArtifactError(category)
    return value


def _expect_non_negative_int(value: Any, category: str, maximum: int = 1_000_000) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ArtifactError(category)
    return value


def _expect_sha256(value: Any, category: str) -> str:
    text = _expect_string(value, category=category, maximum=64)
    if not _SHA256.fullmatch(text):
        raise ArtifactError(category)
    return text


def _expect_fingerprint(value: Any, category: str) -> str:
    text = _expect_string(value, category=category, maximum=71)
    if not _FINGERPRINT.fullmatch(text):
        raise ArtifactError(category)
    return text


def _validate_stage(
    stage: Any,
    *,
    expected_format: str,
    expected_file: str,
    file_bytes: bytes,
    warning_name: str,
    directory: Path,
) -> None:
    base_keys = {"status", "format", "output_file", "output_bytes", "output_sha256"}
    if expected_format == "json":
        base_keys.add("top_level_type")
    warning_keys = {"warnings_file", "warnings_bytes", "warnings_sha256"}
    _expect(isinstance(stage, dict), f"{expected_file}_metadata_shape_invalid")
    actual_keys = set(stage)
    _expect(
        actual_keys == base_keys or actual_keys == base_keys | warning_keys,
        f"{expected_file}_metadata_shape_invalid",
    )
    _expect(stage.get("status") == "succeeded", f"{expected_file}_not_succeeded")
    _expect(stage.get("format") == expected_format, f"{expected_file}_format_mismatch")
    _expect(stage.get("output_file") == expected_file, f"{expected_file}_name_mismatch")
    _expect(stage.get("output_bytes") == len(file_bytes), f"{expected_file}_size_mismatch")
    _expect_sha256(stage.get("output_sha256"), f"{expected_file}_digest_invalid")
    _expect(stage["output_sha256"] == _sha256(file_bytes), f"{expected_file}_digest_mismatch")
    if expected_format == "json":
        _expect(stage.get("top_level_type") == "object", "bundle_plan_type_mismatch")

    warning_path = directory / warning_name
    if warning_keys <= actual_keys:
        _expect(stage.get("warnings_file") == warning_name, f"{warning_name}_name_mismatch")
        warning_bytes = _read_regular_file(
            warning_path, maximum_bytes=MAX_TEXT_BYTES, category=warning_name
        )
        _expect(stage.get("warnings_bytes") == len(warning_bytes), f"{warning_name}_size_mismatch")
        _expect_sha256(stage.get("warnings_sha256"), f"{warning_name}_digest_invalid")
        _expect(stage["warnings_sha256"] == _sha256(warning_bytes), f"{warning_name}_digest_mismatch")
    else:
        _expect(not warning_path.exists(), f"{warning_name}_is_unreferenced")


def _validate_arguments(expected: ExpectedProvenance) -> None:
    _expect(expected.target in {"dev", "prod"}, "expected_target_invalid")
    _expect(bool(_REPOSITORY.fullmatch(expected.repository)), "expected_repository_invalid")
    _expect(expected.ref == "refs/heads/main", "expected_ref_invalid")
    _expect(bool(_COMMIT.fullmatch(expected.commit)), "expected_commit_invalid")
    _expect(bool(_POSITIVE_INTEGER.fullmatch(expected.run_id)), "expected_run_id_invalid")
    _expect(
        bool(_POSITIVE_INTEGER.fullmatch(expected.run_attempt)),
        "expected_run_attempt_invalid",
    )


def _validate_review_metadata(
    metadata: Any,
    *,
    expected: ExpectedProvenance,
    plan_bytes: bytes,
    stored_review: Mapping[str, Any],
) -> None:
    review = _expect_exact_keys(metadata, REVIEW_METADATA_KEYS, "plan_review_metadata_shape_invalid")
    _expect(review.get("status") == "accepted", "plan_review_metadata_not_accepted")
    _expect(review.get("schema_version") == 2, "plan_review_metadata_schema_mismatch")
    _expect(review.get("policy_file") == PLAN_REVIEW_POLICY_PATH, "plan_review_policy_path_mismatch")
    _expect(review.get("json_file") == PLAN_REVIEW_FILE, "plan_review_json_name_mismatch")
    _expect(
        review.get("markdown_file") == PLAN_REVIEW_SUMMARY_FILE,
        "plan_review_markdown_name_mismatch",
    )
    expected_digest = "sha256:" + _sha256(plan_bytes)
    _expect_fingerprint(review.get("plan_sha256"), "plan_review_metadata_digest_invalid")
    _expect(review.get("plan_sha256") == expected_digest, "plan_review_metadata_digest_mismatch")
    _expect(
        review.get("plan_sha256") == stored_review.get("plan_sha256"),
        "plan_review_metadata_review_digest_mismatch",
    )
    resource_count = _expect_non_negative_int(
        review.get("resource_count"), "plan_review_metadata_resource_count_invalid"
    )
    finding_count = _expect_non_negative_int(
        review.get("finding_count"), "plan_review_metadata_finding_count_invalid"
    )
    _expect(resource_count == stored_review.get("resource_count"), "plan_review_metadata_resource_count_mismatch")
    _expect(finding_count == len(stored_review.get("findings", [])), "plan_review_metadata_finding_count_mismatch")
    _expect(finding_count == 0, "plan_review_metadata_has_findings")
    _expect(stored_review.get("target") == expected.target, "plan_review_target_mismatch")
    _expect(stored_review.get("source_commit") == expected.commit, "plan_review_commit_mismatch")


def _stable_review(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "generated_at_utc"}


def _validate_and_recompute_review(
    *,
    directory: Path,
    expected: ExpectedProvenance,
    evidence_review: Any,
    plan_bytes: bytes,
    review_bytes: bytes,
    review_summary_bytes: bytes,
) -> Mapping[str, Any]:
    stored = _parse_json_object(review_bytes, category="plan_review")
    _expect_exact_keys(stored, REVIEW_KEYS, "plan_review_shape_invalid")
    _expect(stored.get("schema_version") == 2, "plan_review_schema_version_mismatch")
    _expect(stored.get("status") == "accepted", "plan_review_not_accepted")
    _expect_string(stored.get("generated_at_utc"), category="plan_review_timestamp_invalid")
    _expect(stored.get("target") == expected.target, "plan_review_target_mismatch")
    _expect(stored.get("source_commit") == expected.commit, "plan_review_commit_mismatch")
    expected_digest = "sha256:" + _sha256(plan_bytes)
    _expect_fingerprint(stored.get("plan_sha256"), "plan_review_digest_invalid")
    _expect(stored.get("plan_sha256") == expected_digest, "plan_review_digest_mismatch")
    _expect(stored.get("plan_bytes") == len(plan_bytes), "plan_review_plan_size_mismatch")
    _expect(isinstance(stored.get("findings"), list), "plan_review_findings_shape_invalid")
    _expect(stored.get("findings") == [], "plan_review_contains_findings")
    _expect_non_negative_int(stored.get("resource_count"), "plan_review_resource_count_invalid")
    _expect_non_negative_int(
        stored.get("permission_sensitive_resources"),
        "plan_review_permission_count_invalid",
    )

    expected_summary = render_plan_review_summary(stored).encode("utf-8")
    _expect(review_summary_bytes == expected_summary, "plan_review_markdown_mismatch")

    try:
        policy = load_plan_review_policy(PLAN_REVIEW_POLICY, expected.target)
        parsed = parse_plan_review(directory / PLAN_FILE, policy=policy)
        recomputed = recompute_plan_review(
            parsed,
            policy=policy,
            target=expected.target,
            source_commit=expected.commit,
        )
    except PlanReviewError:
        raise ArtifactError("plan_review_recomputation_failed") from None

    _expect(recomputed.get("status") == "accepted", "plan_review_recomputation_not_accepted")
    _expect(
        _stable_review(stored) == _stable_review(recomputed),
        "plan_review_recomputation_mismatch",
    )
    _validate_review_metadata(
        evidence_review,
        expected=expected,
        plan_bytes=plan_bytes,
        stored_review=stored,
    )
    return stored


def verify_artifact(directory: Path, expected: ExpectedProvenance) -> dict[str, Any]:
    """Verify one downloaded plan artifact without invoking GitHub or Databricks."""

    _validate_arguments(expected)
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise ArtifactError("artifact_directory_invalid")
        entries = list(directory.iterdir())
    except ArtifactError:
        raise
    except OSError:
        raise ArtifactError("artifact_directory_unreadable") from None

    names: set[str] = set()
    for entry in entries:
        if entry.name in names:
            raise ArtifactError("artifact_contains_duplicate_name")
        names.add(entry.name)
        if entry.is_symlink() or not entry.is_file():
            raise ArtifactError("artifact_contains_non_regular_entry")
    _expect(REQUIRED_FILES <= names, "artifact_required_file_missing")
    _expect(names <= ALLOWED_FILES, "artifact_contains_unexpected_file")

    evidence_bytes = _read_regular_file(
        directory / EVIDENCE_FILE,
        maximum_bytes=MAX_EVIDENCE_BYTES,
        category="evidence_file",
    )
    plan_bytes = _read_regular_file(
        directory / PLAN_FILE,
        maximum_bytes=MAX_PLAN_BYTES,
        category="bundle_plan_file",
    )
    validation_bytes = _read_regular_file(
        directory / VALIDATION_FILE,
        maximum_bytes=MAX_TEXT_BYTES,
        category="bundle_validation_file",
    )
    _read_regular_file(
        directory / SUMMARY_FILE,
        maximum_bytes=MAX_TEXT_BYTES,
        category="summary_file",
    )
    review_bytes = _read_regular_file(
        directory / PLAN_REVIEW_FILE,
        maximum_bytes=MAX_REVIEW_BYTES,
        category="plan_review_file",
    )
    review_summary_bytes = _read_regular_file(
        directory / PLAN_REVIEW_SUMMARY_FILE,
        maximum_bytes=MAX_TEXT_BYTES,
        category="plan_review_summary_file",
    )

    evidence = _parse_json_object(evidence_bytes, category="evidence")
    expected_top_keys = {
        "schema_version",
        "status",
        "mode",
        "target",
        "generated_at_utc",
        "completed_at_utc",
        "github",
        "authentication",
        "identity",
        "validation",
        "plan",
        "review",
    }
    _expect_exact_keys(evidence, expected_top_keys, "evidence_shape_invalid")
    _expect(evidence.get("schema_version") == 2, "evidence_schema_version_mismatch")
    _expect(evidence.get("status") == "succeeded", "plan_evidence_not_succeeded")
    _expect(evidence.get("mode") == "plan", "plan_evidence_mode_mismatch")
    _expect(evidence.get("target") == expected.target, "plan_evidence_target_mismatch")
    _expect_string(evidence.get("generated_at_utc"), category="generated_timestamp_invalid")
    _expect_string(evidence.get("completed_at_utc"), category="completed_timestamp_invalid")

    github = _expect_exact_keys(
        evidence.get("github"),
        {"repository", "ref", "commit_sha", "run_id", "run_attempt", "workflow"},
        "github_provenance_shape_invalid",
    )
    _expect(github.get("repository") == expected.repository, "repository_provenance_mismatch")
    _expect(github.get("ref") == expected.ref, "ref_provenance_mismatch")
    _expect(github.get("commit_sha") == expected.commit, "commit_provenance_mismatch")
    _expect(str(github.get("run_id")) == expected.run_id, "run_id_provenance_mismatch")
    _expect(
        str(github.get("run_attempt")) == expected.run_attempt,
        "run_attempt_provenance_mismatch",
    )
    _expect_string(github.get("workflow"), category="workflow_provenance_invalid")

    authentication = _expect_exact_keys(
        evidence.get("authentication"),
        {"auth_type", "host_fingerprint", "configured_client_id_fingerprint"},
        "authentication_shape_invalid",
    )
    _expect(authentication.get("auth_type") == "github-oidc", "authentication_type_mismatch")
    _expect_fingerprint(authentication.get("host_fingerprint"), "host_fingerprint_invalid")
    _expect_fingerprint(
        authentication.get("configured_client_id_fingerprint"),
        "client_id_fingerprint_invalid",
    )

    identity = _expect_exact_keys(
        evidence.get("identity"),
        {"status", "active", "application_id_fingerprint", "principal_id_fingerprint"},
        "identity_shape_invalid",
    )
    _expect(identity.get("status") == "succeeded", "identity_not_succeeded")
    _expect(identity.get("active") is True, "identity_not_active")
    _expect_fingerprint(identity.get("application_id_fingerprint"), "application_fingerprint_invalid")
    _expect_fingerprint(identity.get("principal_id_fingerprint"), "principal_fingerprint_invalid")

    _validate_stage(
        evidence.get("validation"),
        expected_format="text",
        expected_file=VALIDATION_FILE,
        file_bytes=validation_bytes,
        warning_name=VALIDATION_WARNING_FILE,
        directory=directory,
    )
    _validate_stage(
        evidence.get("plan"),
        expected_format="json",
        expected_file=PLAN_FILE,
        file_bytes=plan_bytes,
        warning_name=PLAN_WARNING_FILE,
        directory=directory,
    )
    _parse_json_object(plan_bytes, category="bundle_plan")
    stored_review = _validate_and_recompute_review(
        directory=directory,
        expected=expected,
        evidence_review=evidence.get("review"),
        plan_bytes=plan_bytes,
        review_bytes=review_bytes,
        review_summary_bytes=review_summary_bytes,
    )

    return {
        "status": "verified",
        "target": expected.target,
        "commit": expected.commit,
        "run_id": expected.run_id,
        "run_attempt": expected.run_attempt,
        "plan_sha256": _sha256(plan_bytes),
        "review_status": stored_review["status"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--expected-target", required=True, choices=("dev", "prod"))
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-run-attempt", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected = ExpectedProvenance(
        target=args.expected_target,
        repository=args.expected_repository,
        ref=args.expected_ref,
        commit=args.expected_commit,
        run_id=args.expected_run_id,
        run_attempt=args.expected_run_attempt,
    )
    try:
        result = verify_artifact(args.artifact_dir, expected)
    except ArtifactError as error:
        print(f"Reviewed bundle plan verification failed: {error.category}", file=sys.stderr)
        return 1
    print(
        "Reviewed bundle plan verified: "
        f"target={result['target']} plan_sha256={result['plan_sha256']} "
        f"review_status={result['review_status']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
