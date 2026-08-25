#!/usr/bin/env python3
"""Verify one evidence-bound controlled-development runtime manifest."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

_CORE_PATH = Path(__file__).with_name("development_runtime_evidence_core.py")
_CORE_SPEC = importlib.util.spec_from_file_location(
    "_development_runtime_evidence_core", _CORE_PATH
)
if _CORE_SPEC is None or _CORE_SPEC.loader is None:
    raise RuntimeError("development_runtime_evidence_core_unavailable")
_core = importlib.util.module_from_spec(_CORE_SPEC)
sys.modules[_CORE_SPEC.name] = _core
_CORE_SPEC.loader.exec_module(_core)

DEFAULT_MAX_AGE_HOURS = _core.DEFAULT_MAX_AGE_HOURS
DEFAULT_MAX_EXECUTION_HOURS = _core.DEFAULT_MAX_EXECUTION_HOURS
FUTURE_TOLERANCE = _core.FUTURE_TOLERANCE
MAX_INPUT_BYTES = _core.MAX_INPUT_BYTES
MAX_STRING_BYTES = _core.MAX_STRING_BYTES
MAX_FINDINGS = _core.MAX_FINDINGS
MAX_RECORD_COUNT = _core.MAX_RECORD_COUNT
OUTPUT_JSON = "development-runtime-verification.json"
OUTPUT_MARKDOWN = "development-runtime-verification.md"
if OUTPUT_JSON != _core.OUTPUT_JSON or OUTPUT_MARKDOWN != _core.OUTPUT_MARKDOWN:
    raise RuntimeError("development_runtime_output_contract_mismatch")
EXPECTED_REPOSITORY = _core.EXPECTED_REPOSITORY
REQUIRED_FAMILIES = _core.REQUIRED_FAMILIES
ASSERTION_FAMILIES = _core.ASSERTION_FAMILIES
ALLOWED_ASSERTION_STATUSES = _core.ALLOWED_ASSERTION_STATUSES
VerificationError = _core.VerificationError
positive_hours = _core.positive_hours

# Kept explicit so source contracts can verify the inherited fail-closed boundary.
INHERITED_BLOCKING_CATEGORIES = (
    "production_contact_was_reported",
    "rollback_was_not_tested",
)

_EXECUTION_KEYS = {
    "execution_fingerprint",
    "evidence_sha256",
    "started_at_utc",
    "completed_at_utc",
    "production_contact",
    "deployment_principal_fingerprint",
    "runtime_principal_fingerprint",
}


def _validate_shape(document: Mapping[str, Any]) -> dict[str, Any]:
    """Require protected execution evidence before delegating the stable schema."""

    execution = document.get("execution")
    if not isinstance(execution, dict) or set(execution) != _EXECUTION_KEYS:
        raise VerificationError("execution_shape_invalid")
    execution_evidence = _core._fingerprint(
        execution.get("evidence_sha256"),
        category="execution_evidence_digest_invalid",
    )

    delegated_document = dict(document)
    delegated_execution = dict(execution)
    delegated_execution.pop("evidence_sha256")
    delegated_document["execution"] = delegated_execution
    normalized = _core._validate_shape(delegated_document)
    normalized["execution"]["evidence_sha256"] = execution_evidence
    return normalized


def verify_evidence(
    evidence_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    max_execution_hours: float = DEFAULT_MAX_EXECUTION_HOURS,
) -> dict[str, Any]:
    """Verify one manifest while preserving the exact input-file digest."""

    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise VerificationError("max_age_hours_invalid")
    if not math.isfinite(max_execution_hours) or max_execution_hours <= 0:
        raise VerificationError("max_execution_hours_invalid")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise VerificationError("verification_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)

    evidence_bytes = _core._read_regular_bytes(
        evidence_path, category="evidence_file_invalid"
    )
    evidence = _validate_shape(
        _core._parse_json_object(evidence_bytes, category="evidence")
    )
    findings = _core._verify_semantics(
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
        "generated_at_utc": _core._render_timestamp(reference_time),
        "target": evidence["target"],
        "repository": evidence["repository"],
        "source_commit": evidence["source_commit"],
        "captured_at_utc": evidence["captured_at_text"],
        "evidence_sha256": _core._sha256(evidence_bytes),
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
            "evidence_sha256": evidence["execution"]["evidence_sha256"],
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
    rendered = _core.render_markdown(report)
    marker = f"- Evidence manifest: `{report['evidence_sha256']}`\n"
    addition = f"- Execution evidence: `{report['execution']['evidence_sha256']}`\n"
    if marker not in rendered:
        raise VerificationError("verification_markdown_template_mismatch")
    return rendered.replace(marker, marker + addition, 1)


def write_outputs(output_directory: Path, report: Mapping[str, Any]) -> None:
    directory = _core._prepare_output_directory(output_directory)
    _core._write_text_atomic(
        directory / OUTPUT_JSON,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    _core._write_text_atomic(directory / OUTPUT_MARKDOWN, render_markdown(report))


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
