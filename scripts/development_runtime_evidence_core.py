#!/usr/bin/env python3
"""Verify one bounded controlled-development runtime evidence manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MAX_AGE_HOURS = 72.0
DEFAULT_MAX_EXECUTION_HOURS = 4.0
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_INPUT_BYTES = 1_000_000
MAX_STRING_BYTES = 512
MAX_FINDINGS = 128
MAX_RECORD_COUNT = 10**12
OUTPUT_JSON = "development-runtime-verification.json"
OUTPUT_MARKDOWN = "development-runtime-verification.md"
EXPECTED_REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"

REQUIRED_FAMILIES = (
    "bronze",
    "silver",
    "gold",
    "forecast",
    "warehouse",
    "quality",
    "expectations",
    "queries",
    "grants",
)
ASSERTION_FAMILIES = {
    "source_upload_is_immutable": "bronze",
    "checkpoint_is_reused": "bronze",
    "identical_replay_is_idempotent": "bronze",
    "conflicting_event_is_quarantined": "silver",
    "silver_publication_is_committed": "silver",
    "gold_publication_is_committed": "gold",
    "forecast_publication_is_committed": "forecast",
    "warehouse_publication_is_committed": "warehouse",
    "current_views_expose_committed_generations_only": "warehouse",
    "source_to_target_is_reconciled": "quality",
    "quality_evidence_is_persisted": "quality",
    "expectations_are_evaluated": "expectations",
    "saved_queries_are_viewer_run": "queries",
    "effective_grants_are_verified": "grants",
    "deployment_denials_are_verified": "grants",
    "runtime_denials_are_verified": "grants",
}
ALLOWED_ASSERTION_STATUSES = {"passed", "failed", "not_tested"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_]{2,127}\Z")


class VerificationError(RuntimeError):
    """Stable invalid-input category safe to expose in logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def positive_hours(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of hours") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of hours")
    return parsed


def _expect(condition: bool, category: str) -> None:
    if not condition:
        raise VerificationError(category)


def _read_regular_bytes(path: Path, *, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise VerificationError(category)
        size = path.stat().st_size
        if size < 1 or size > MAX_INPUT_BYTES:
            raise VerificationError(f"{category}_size_invalid")
        payload = path.read_bytes()
        if not payload or len(payload) > MAX_INPUT_BYTES:
            raise VerificationError(f"{category}_size_invalid")
        return payload
    except VerificationError:
        raise
    except OSError:
        raise VerificationError(f"{category}_unreadable") from None


def _parse_json_object(payload: bytes, *, category: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VerificationError(f"{category}_invalid_json") from None
    if not isinstance(document, dict):
        raise VerificationError(f"{category}_unexpected_shape")
    return document


def _exact_mapping(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise VerificationError(category)
    return value


def _string(value: Any, *, category: str, maximum_bytes: int = MAX_STRING_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise VerificationError(category)
    return value


def _timestamp(value: Any, *, category: str) -> datetime:
    text = _string(value, category=category, maximum_bytes=64)
    if not text.endswith("Z"):
        raise VerificationError(category)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise VerificationError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise VerificationError(category)
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: Any, *, category: str) -> str:
    text = _string(value, category=category, maximum_bytes=71)
    if not _SHA256.fullmatch(text):
        raise VerificationError(category)
    return text


def _non_negative_int(value: Any, *, category: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_RECORD_COUNT
    ):
        raise VerificationError(category)
    return value


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_shape(document: Mapping[str, Any]) -> dict[str, Any]:
    _exact_mapping(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "source_commit",
            "captured_at_utc",
            "apply",
            "execution",
            "evidence_families",
            "assertions",
            "rollback",
        },
        "evidence_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "evidence_schema_version_mismatch")
    _expect(document.get("target") == "dev", "evidence_target_must_be_dev")
    _expect(document.get("repository") == EXPECTED_REPOSITORY, "evidence_repository_mismatch")
    source_commit = _string(
        document.get("source_commit"),
        category="source_commit_invalid",
        maximum_bytes=40,
    )
    _expect(bool(_COMMIT.fullmatch(source_commit)), "source_commit_invalid")
    captured_at = _timestamp(
        document.get("captured_at_utc"), category="captured_at_utc_invalid"
    )

    apply = _exact_mapping(
        document.get("apply"),
        {
            "authorized",
            "approved_at_utc",
            "approval_sha256",
            "accepted_plan_sha256",
            "accepted_plan_review_sha256",
            "workflow_run_fingerprint",
        },
        "apply_shape_invalid",
    )
    if not isinstance(apply.get("authorized"), bool):
        raise VerificationError("apply_authorized_invalid")
    normalized_apply = {
        "authorized": apply["authorized"],
        "approved_at_utc": _timestamp(
            apply.get("approved_at_utc"), category="approved_at_utc_invalid"
        ),
        "approved_at_text": apply["approved_at_utc"],
        "approval_sha256": _fingerprint(
            apply.get("approval_sha256"), category="approval_sha256_invalid"
        ),
        "accepted_plan_sha256": _fingerprint(
            apply.get("accepted_plan_sha256"), category="accepted_plan_sha256_invalid"
        ),
        "accepted_plan_review_sha256": _fingerprint(
            apply.get("accepted_plan_review_sha256"),
            category="accepted_plan_review_sha256_invalid",
        ),
        "workflow_run_fingerprint": _fingerprint(
            apply.get("workflow_run_fingerprint"),
            category="workflow_run_fingerprint_invalid",
        ),
    }

    execution = _exact_mapping(
        document.get("execution"),
        {
            "execution_fingerprint",
            "started_at_utc",
            "completed_at_utc",
            "production_contact",
            "deployment_principal_fingerprint",
            "runtime_principal_fingerprint",
        },
        "execution_shape_invalid",
    )
    if not isinstance(execution.get("production_contact"), bool):
        raise VerificationError("production_contact_invalid")
    normalized_execution = {
        "execution_fingerprint": _fingerprint(
            execution.get("execution_fingerprint"),
            category="execution_fingerprint_invalid",
        ),
        "started_at_utc": _timestamp(
            execution.get("started_at_utc"), category="execution_started_at_invalid"
        ),
        "started_at_text": execution["started_at_utc"],
        "completed_at_utc": _timestamp(
            execution.get("completed_at_utc"), category="execution_completed_at_invalid"
        ),
        "completed_at_text": execution["completed_at_utc"],
        "production_contact": execution["production_contact"],
        "deployment_principal_fingerprint": _fingerprint(
            execution.get("deployment_principal_fingerprint"),
            category="deployment_principal_fingerprint_invalid",
        ),
        "runtime_principal_fingerprint": _fingerprint(
            execution.get("runtime_principal_fingerprint"),
            category="runtime_principal_fingerprint_invalid",
        ),
    }

    raw_families = document.get("evidence_families")
    if not isinstance(raw_families, list) or len(raw_families) > 32:
        raise VerificationError("evidence_families_shape_invalid")
    families: list[dict[str, Any]] = []
    family_names: set[str] = set()
    for raw_family in raw_families:
        family = _exact_mapping(
            raw_family,
            {
                "family",
                "execution_fingerprint",
                "observed_at_utc",
                "evidence_sha256",
                "record_count",
            },
            "evidence_family_shape_invalid",
        )
        name = _string(family.get("family"), category="evidence_family_name_invalid")
        _expect(name in REQUIRED_FAMILIES, "evidence_family_name_unsupported")
        _expect(name not in family_names, "evidence_family_duplicate")
        family_names.add(name)
        families.append(
            {
                "family": name,
                "execution_fingerprint": _fingerprint(
                    family.get("execution_fingerprint"),
                    category="evidence_family_execution_fingerprint_invalid",
                ),
                "observed_at_utc": _timestamp(
                    family.get("observed_at_utc"),
                    category="evidence_family_timestamp_invalid",
                ),
                "observed_at_text": family["observed_at_utc"],
                "evidence_sha256": _fingerprint(
                    family.get("evidence_sha256"),
                    category="evidence_family_digest_invalid",
                ),
                "record_count": _non_negative_int(
                    family.get("record_count"), category="evidence_family_record_count_invalid"
                ),
            }
        )

    raw_assertions = document.get("assertions")
    if not isinstance(raw_assertions, list) or len(raw_assertions) > 64:
        raise VerificationError("assertions_shape_invalid")
    assertions: list[dict[str, Any]] = []
    assertion_ids: set[str] = set()
    for raw_assertion in raw_assertions:
        assertion = _exact_mapping(
            raw_assertion,
            {
                "assertion_id",
                "family",
                "execution_fingerprint",
                "status",
                "observed_at_utc",
                "evidence_sha256",
            },
            "assertion_shape_invalid",
        )
        assertion_id = _string(
            assertion.get("assertion_id"), category="assertion_id_invalid", maximum_bytes=128
        )
        _expect(bool(_NAME.fullmatch(assertion_id)), "assertion_id_invalid")
        _expect(assertion_id in ASSERTION_FAMILIES, "assertion_id_unsupported")
        _expect(assertion_id not in assertion_ids, "assertion_id_duplicate")
        assertion_ids.add(assertion_id)
        family = _string(assertion.get("family"), category="assertion_family_invalid")
        _expect(family in REQUIRED_FAMILIES, "assertion_family_unsupported")
        status = _string(assertion.get("status"), category="assertion_status_invalid")
        _expect(status in ALLOWED_ASSERTION_STATUSES, "assertion_status_invalid")
        assertions.append(
            {
                "assertion_id": assertion_id,
                "family": family,
                "execution_fingerprint": _fingerprint(
                    assertion.get("execution_fingerprint"),
                    category="assertion_execution_fingerprint_invalid",
                ),
                "status": status,
                "observed_at_utc": _timestamp(
                    assertion.get("observed_at_utc"),
                    category="assertion_timestamp_invalid",
                ),
                "observed_at_text": assertion["observed_at_utc"],
                "evidence_sha256": _fingerprint(
                    assertion.get("evidence_sha256"),
                    category="assertion_evidence_digest_invalid",
                ),
            }
        )

    rollback = _exact_mapping(
        document.get("rollback"),
        {"tested", "completed_at_utc", "evidence_sha256", "recovery_point_sha256"},
        "rollback_shape_invalid",
    )
    if not isinstance(rollback.get("tested"), bool):
        raise VerificationError("rollback_tested_invalid")
    normalized_rollback = {
        "tested": rollback["tested"],
        "completed_at_utc": _timestamp(
            rollback.get("completed_at_utc"), category="rollback_completed_at_invalid"
        ),
        "completed_at_text": rollback["completed_at_utc"],
        "evidence_sha256": _fingerprint(
            rollback.get("evidence_sha256"), category="rollback_evidence_digest_invalid"
        ),
        "recovery_point_sha256": _fingerprint(
            rollback.get("recovery_point_sha256"),
            category="rollback_recovery_point_digest_invalid",
        ),
    }

    return {
        "target": "dev",
        "repository": EXPECTED_REPOSITORY,
        "source_commit": source_commit,
        "captured_at_utc": captured_at,
        "captured_at_text": document["captured_at_utc"],
        "apply": normalized_apply,
        "execution": normalized_execution,
        "families": families,
        "assertions": assertions,
        "rollback": normalized_rollback,
    }


def _finding(category: str, scope: str | None = None) -> dict[str, str]:
    finding = {"category": category}
    if scope is not None:
        finding["scope"] = scope
    return finding


def _within_runtime_window(
    timestamp: datetime,
    *,
    execution_start: datetime,
    captured_at: datetime,
    now: datetime,
    oldest_allowed: datetime,
) -> bool:
    return (
        oldest_allowed <= timestamp <= now + FUTURE_TOLERANCE
        and execution_start - FUTURE_TOLERANCE <= timestamp
        and timestamp <= captured_at + FUTURE_TOLERANCE
    )


def _verify_semantics(
    evidence: Mapping[str, Any],
    *,
    now: datetime,
    max_age_hours: float,
    max_execution_hours: float,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    oldest_allowed = now - timedelta(hours=max_age_hours)
    captured_at = evidence["captured_at_utc"]
    apply = evidence["apply"]
    execution = evidence["execution"]
    execution_start = execution["started_at_utc"]
    execution_end = execution["completed_at_utc"]

    if captured_at < oldest_allowed:
        findings.append(_finding("evidence_capture_is_stale"))
    if captured_at > now + FUTURE_TOLERANCE:
        findings.append(_finding("evidence_capture_is_in_future"))
    if not apply["authorized"]:
        findings.append(_finding("development_apply_was_not_authorized"))
    if apply["approved_at_utc"] > execution_start + FUTURE_TOLERANCE:
        findings.append(_finding("approval_occurred_after_execution_started"))
    if apply["approved_at_utc"] < oldest_allowed:
        findings.append(_finding("development_apply_approval_is_stale"))
    if execution["production_contact"]:
        findings.append(_finding("production_contact_was_reported"))
    if (
        execution["deployment_principal_fingerprint"]
        == execution["runtime_principal_fingerprint"]
    ):
        findings.append(_finding("deployment_and_runtime_identities_overlap"))
    if execution_end < execution_start:
        findings.append(_finding("execution_completed_before_it_started"))
    elif execution_end - execution_start > timedelta(hours=max_execution_hours):
        findings.append(_finding("execution_duration_exceeds_limit"))
    if execution_start < oldest_allowed or execution_end < oldest_allowed:
        findings.append(_finding("execution_is_stale"))
    if execution_start > now + FUTURE_TOLERANCE or execution_end > now + FUTURE_TOLERANCE:
        findings.append(_finding("execution_is_in_future"))
    if execution_end > captured_at + FUTURE_TOLERANCE:
        findings.append(_finding("execution_completed_after_capture"))

    expected_execution = execution["execution_fingerprint"]
    families_by_name = {family["family"]: family for family in evidence["families"]}
    for family_name in REQUIRED_FAMILIES:
        family = families_by_name.get(family_name)
        if family is None:
            findings.append(_finding("required_evidence_family_missing", family_name))
            continue
        if family["execution_fingerprint"] != expected_execution:
            findings.append(_finding("evidence_family_execution_mismatch", family_name))
        if family["record_count"] < 1:
            findings.append(_finding("evidence_family_is_empty", family_name))
        if not _within_runtime_window(
            family["observed_at_utc"],
            execution_start=execution_start,
            captured_at=captured_at,
            now=now,
            oldest_allowed=oldest_allowed,
        ):
            findings.append(_finding("evidence_family_timestamp_outside_window", family_name))

    assertions_by_id = {
        assertion["assertion_id"]: assertion for assertion in evidence["assertions"]
    }
    for assertion_id, required_family in ASSERTION_FAMILIES.items():
        assertion = assertions_by_id.get(assertion_id)
        if assertion is None:
            findings.append(_finding("required_assertion_missing", assertion_id))
            continue
        if assertion["family"] != required_family:
            findings.append(_finding("assertion_family_mismatch", assertion_id))
        if assertion["execution_fingerprint"] != expected_execution:
            findings.append(_finding("assertion_execution_mismatch", assertion_id))
        if assertion["status"] == "failed":
            findings.append(_finding("runtime_assertion_failed", assertion_id))
        elif assertion["status"] == "not_tested":
            findings.append(_finding("runtime_assertion_not_tested", assertion_id))
        if not _within_runtime_window(
            assertion["observed_at_utc"],
            execution_start=execution_start,
            captured_at=captured_at,
            now=now,
            oldest_allowed=oldest_allowed,
        ):
            findings.append(_finding("assertion_timestamp_outside_window", assertion_id))

    rollback = evidence["rollback"]
    if not rollback["tested"]:
        findings.append(_finding("rollback_was_not_tested"))
    if rollback["completed_at_utc"] < execution_end - FUTURE_TOLERANCE:
        findings.append(_finding("rollback_completed_before_execution"))
    if rollback["completed_at_utc"] > captured_at + FUTURE_TOLERANCE:
        findings.append(_finding("rollback_completed_after_capture"))
    if rollback["completed_at_utc"] > now + FUTURE_TOLERANCE:
        findings.append(_finding("rollback_is_in_future"))
    if rollback["completed_at_utc"] < oldest_allowed:
        findings.append(_finding("rollback_evidence_is_stale"))

    deduplicated = {
        (finding["category"], finding.get("scope", "")): finding for finding in findings
    }
    ordered = [deduplicated[key] for key in sorted(deduplicated)]
    if len(ordered) > MAX_FINDINGS:
        ordered = ordered[: MAX_FINDINGS - 1]
        ordered.append(_finding("findings_truncated"))
    return ordered


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verify_evidence(
    evidence_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    max_execution_hours: float = DEFAULT_MAX_EXECUTION_HOURS,
) -> dict[str, Any]:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise VerificationError("max_age_hours_invalid")
    if not math.isfinite(max_execution_hours) or max_execution_hours <= 0:
        raise VerificationError("max_execution_hours_invalid")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise VerificationError("verification_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)

    evidence_bytes = _read_regular_bytes(evidence_path, category="evidence_file_invalid")
    evidence = _validate_shape(_parse_json_object(evidence_bytes, category="evidence"))
    findings = _verify_semantics(
        evidence,
        now=reference_time,
        max_age_hours=max_age_hours,
        max_execution_hours=max_execution_hours,
    )
    families = [
        {
            "family": item["family"],
            "execution_fingerprint": item["execution_fingerprint"],
            "observed_at_utc": item["observed_at_text"],
            "evidence_sha256": item["evidence_sha256"],
            "record_count": item["record_count"],
        }
        for item in sorted(evidence["families"], key=lambda item: item["family"])
    ]
    assertions = [
        {
            "assertion_id": item["assertion_id"],
            "family": item["family"],
            "execution_fingerprint": item["execution_fingerprint"],
            "status": item["status"],
            "observed_at_utc": item["observed_at_text"],
            "evidence_sha256": item["evidence_sha256"],
        }
        for item in sorted(evidence["assertions"], key=lambda item: item["assertion_id"])
    ]
    return {
        "schema_version": 1,
        "status": "verified" if not findings else "blocked",
        "generated_at_utc": _render_timestamp(reference_time),
        "target": evidence["target"],
        "repository": evidence["repository"],
        "source_commit": evidence["source_commit"],
        "captured_at_utc": evidence["captured_at_text"],
        "evidence_sha256": _sha256(evidence_bytes),
        "max_age_hours": max_age_hours,
        "max_execution_hours": max_execution_hours,
        "apply": {
            "authorized": evidence["apply"]["authorized"],
            "approved_at_utc": evidence["apply"]["approved_at_text"],
            "approval_sha256": evidence["apply"]["approval_sha256"],
            "accepted_plan_sha256": evidence["apply"]["accepted_plan_sha256"],
            "accepted_plan_review_sha256": evidence["apply"][
                "accepted_plan_review_sha256"
            ],
            "workflow_run_fingerprint": evidence["apply"]["workflow_run_fingerprint"],
        },
        "execution": {
            "execution_fingerprint": evidence["execution"]["execution_fingerprint"],
            "started_at_utc": evidence["execution"]["started_at_text"],
            "completed_at_utc": evidence["execution"]["completed_at_text"],
            "production_contact": evidence["execution"]["production_contact"],
            "deployment_principal_fingerprint": evidence["execution"][
                "deployment_principal_fingerprint"
            ],
            "runtime_principal_fingerprint": evidence["execution"][
                "runtime_principal_fingerprint"
            ],
        },
        "evidence_family_count": len(families),
        "required_evidence_family_count": len(REQUIRED_FAMILIES),
        "evidence_families": families,
        "assertion_count": len(assertions),
        "required_assertion_count": len(ASSERTION_FAMILIES),
        "assertions": assertions,
        "rollback": {
            "tested": evidence["rollback"]["tested"],
            "completed_at_utc": evidence["rollback"]["completed_at_text"],
            "evidence_sha256": evidence["rollback"]["evidence_sha256"],
            "recovery_point_sha256": evidence["rollback"]["recovery_point_sha256"],
        },
        "findings": findings,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Controlled development runtime verification",
        "",
        f"- Status: **{report['status']}**",
        f"- Target: `{report['target']}`",
        f"- Repository: `{report['repository']}`",
        f"- Source commit: `{report['source_commit']}`",
        f"- Captured: `{report['captured_at_utc']}`",
        f"- Evidence manifest: `{report['evidence_sha256']}`",
        (
            "- Evidence families: "
            f"`{report['evidence_family_count']}/{report['required_evidence_family_count']}`"
        ),
        (
            "- Assertions: "
            f"`{report['assertion_count']}/{report['required_assertion_count']}`"
        ),
        f"- Rollback tested: `{str(report['rollback']['tested']).lower()}`",
        "",
        "## Findings",
        "",
    ]
    if report["findings"]:
        for finding in report["findings"]:
            scope = f" (`{finding['scope']}`)" if finding.get("scope") else ""
            lines.append(f"- `{finding['category']}`{scope}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Assertion results",
            "",
            "| Assertion | Family | Status |",
            "| --- | --- | --- |",
        ]
    )
    for assertion in report["assertions"]:
        lines.append(
            f"| `{assertion['assertion_id']}` | `{assertion['family']}` | "
            f"`{assertion['status']}` |"
        )
    lines.extend(
        [
            "",
            "The report contains fingerprints, digests, counts and timestamps, not raw "
            "principal IDs, resource names, table contents, provider responses or credentials.",
            "Verification admits supplied evidence for human review; it does not authorize "
            "another deployment or prove that the evidence was collected honestly.",
            "",
        ]
    )
    return "\n".join(lines)


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise VerificationError("output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise VerificationError("output_directory_could_not_be_created") from None
    if path.is_symlink() or not path.is_dir():
        raise VerificationError("output_directory_invalid")
    return path


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise VerificationError("verification_temporary_file_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise VerificationError("verification_output_path_invalid")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise VerificationError("verification_output_write_failed") from None


def write_outputs(output_directory: Path, report: Mapping[str, Any]) -> None:
    directory = _prepare_output_directory(output_directory)
    _write_text_atomic(
        directory / OUTPUT_JSON,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    _write_text_atomic(directory / OUTPUT_MARKDOWN, render_markdown(report))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--max-age-hours", type=positive_hours, default=DEFAULT_MAX_AGE_HOURS
    )
    parser.add_argument(
        "--max-execution-hours",
        type=positive_hours,
        default=DEFAULT_MAX_EXECUTION_HOURS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = verify_evidence(
            args.evidence,
            max_age_hours=args.max_age_hours,
            max_execution_hours=args.max_execution_hours,
        )
        write_outputs(args.output_dir, report)
    except VerificationError as error:
        print(
            f"Development runtime evidence verification failed: {error.category}",
            file=sys.stderr,
        )
        return 2
    print(
        "Development runtime evidence verification "
        f"{report['status']}: assertions={report['assertion_count']}/"
        f"{report['required_assertion_count']}"
    )
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
