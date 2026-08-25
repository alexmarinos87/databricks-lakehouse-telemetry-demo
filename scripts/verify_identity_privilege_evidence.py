#!/usr/bin/env python3
"""Verify bounded development evidence for deployment/runtime least privilege."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_CONTRACT = (
    Path(__file__).resolve().parents[1] / "config" / "identity_privilege_contract.json"
)
DEFAULT_MAX_AGE_HOURS = 72.0
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_INPUT_BYTES = 1_000_000
MAX_OBSERVATIONS = 64
MAX_FINDINGS = 128
MAX_STRING_BYTES = 512
OUTPUT_JSON = "identity-privilege-verification.json"
OUTPUT_MARKDOWN = "identity-privilege-verification.md"
EXPECTED_REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
ALLOWED_METHODS = {
    "workflow_run",
    "resource_readback",
    "permission_readback",
    "denied_live_attempt",
}
ALLOWED_OUTCOMES = {"succeeded", "denied", "error", "not_tested"}
ALLOWED_EXPECTATIONS = {"allowed", "denied"}
IDENTITY_NAMES = {"deployment", "runtime"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_EVIDENCE_ID = re.compile(r"[a-z][a-z0-9_]{2,127}\Z")

REQUIRED_EVIDENCE_RULES: dict[str, dict[str, Any]] = {
    "deployment_principal_can_assign_runtime_service_principal": {
        "identity": "deployment",
        "capabilities": ("manage_bundle_jobs_and_pipelines",),
        "expectation": "allowed",
        "methods": {"resource_readback"},
    },
    "runtime_principal_can_execute_job_and_pipeline": {
        "identity": "runtime",
        "capabilities": ("run_lakehouse_job", "run_quality_pipeline"),
        "expectation": "allowed",
        "methods": {"workflow_run"},
    },
    "deployment_principal_cannot_select_curated_tables": {
        "identity": "deployment",
        "capabilities": ("select_curated_tables",),
        "expectation": "denied",
        "methods": {"denied_live_attempt"},
    },
    "runtime_principal_cannot_deploy_bundle": {
        "identity": "runtime",
        "capabilities": ("bundle_deploy",),
        "expectation": "denied",
        "methods": {"permission_readback"},
    },
    "deployment_principal_cannot_run_job_as_itself": {
        "identity": "deployment",
        "capabilities": ("run_lakehouse_job_as_self",),
        "expectation": "denied",
        "methods": {"resource_readback"},
    },
}


class VerificationError(RuntimeError):
    """Stable invalid-input category safe to expose in workflow logs."""

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


def _regular_bytes(path: Path, *, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise VerificationError(category)
        size = path.stat().st_size
        if size < 1 or size > MAX_INPUT_BYTES:
            raise VerificationError(f"{category}_size_invalid")
        return path.read_bytes()
    except VerificationError:
        raise
    except OSError:
        raise VerificationError(f"{category}_unreadable") from None


def _parse_json_object(value: bytes, *, category: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VerificationError(f"{category}_invalid_json") from None
    if not isinstance(parsed, dict):
        raise VerificationError(f"{category}_unexpected_shape")
    return parsed


def _exact_mapping(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise VerificationError(category)
    return value


def _string(
    value: Any,
    *,
    category: str,
    maximum_bytes: int = MAX_STRING_BYTES,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise VerificationError(category)
    return value


def _string_list(
    value: Any,
    *,
    category: str,
    maximum_items: int = 64,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum_items:
        raise VerificationError(category)
    items = tuple(_string(item, category=category) for item in value)
    if len(set(items)) != len(items):
        raise VerificationError(category)
    return items


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


def _fingerprint(value: Any, category: str) -> str:
    text = _string(value, category=category, maximum_bytes=71)
    if not _SHA256.fullmatch(text):
        raise VerificationError(category)
    return text


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_contract(document: Mapping[str, Any]) -> dict[str, Any]:
    _exact_mapping(
        document,
        {"schema_version", "identities", "required_external_evidence", "known_exception"},
        "contract_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "contract_schema_version_mismatch")
    identities = _exact_mapping(
        document.get("identities"), IDENTITY_NAMES, "contract_identities_shape_invalid"
    )
    normalized: dict[str, Any] = {"identities": {}}
    for identity_name in sorted(IDENTITY_NAMES):
        identity = _exact_mapping(
            identities.get(identity_name),
            {"principal_name", "allowed_capabilities", "denied_capabilities"},
            "contract_identity_shape_invalid",
        )
        principal_name = _string(
            identity.get("principal_name"), category="contract_principal_name_invalid"
        )
        allowed = _string_list(
            identity.get("allowed_capabilities"),
            category="contract_allowed_capabilities_invalid",
        )
        denied = _string_list(
            identity.get("denied_capabilities"),
            category="contract_denied_capabilities_invalid",
        )
        _expect(not (set(allowed) & set(denied)), "contract_capability_overlap")
        normalized["identities"][identity_name] = {
            "principal_name": principal_name,
            "allowed": set(allowed),
            "denied": set(denied),
        }

    required = _string_list(
        document.get("required_external_evidence"),
        category="contract_required_evidence_invalid",
    )
    _expect(
        set(required) == set(REQUIRED_EVIDENCE_RULES),
        "required_evidence_contract_unsupported",
    )
    known_exception = _exact_mapping(
        document.get("known_exception"),
        {"capability", "reason", "constraints"},
        "contract_known_exception_shape_invalid",
    )
    exception_capability = _string(
        known_exception.get("capability"), category="contract_exception_capability_invalid"
    )
    _string(known_exception.get("reason"), category="contract_exception_reason_invalid")
    _string_list(
        known_exception.get("constraints"),
        category="contract_exception_constraints_invalid",
    )
    _expect(
        exception_capability in normalized["identities"]["deployment"]["allowed"],
        "contract_exception_capability_not_allowed",
    )

    for rule in REQUIRED_EVIDENCE_RULES.values():
        identity = normalized["identities"][rule["identity"]]
        expected_set = (
            identity["allowed"] if rule["expectation"] == "allowed" else identity["denied"]
        )
        _expect(
            set(rule["capabilities"]) <= expected_set,
            "required_evidence_capability_not_in_contract",
        )
    normalized["required"] = tuple(required)
    return normalized


def _validate_evidence_shape(document: Mapping[str, Any]) -> dict[str, Any]:
    _exact_mapping(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "source_commit",
            "captured_at_utc",
            "workspace_fingerprint",
            "identities",
            "observations",
        },
        "evidence_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "evidence_schema_version_mismatch")
    _expect(document.get("target") == "dev", "evidence_target_must_be_dev")
    _expect(
        document.get("repository") == EXPECTED_REPOSITORY,
        "evidence_repository_mismatch",
    )
    source_commit = _string(
        document.get("source_commit"), category="evidence_source_commit_invalid", maximum_bytes=40
    )
    _expect(bool(_COMMIT.fullmatch(source_commit)), "evidence_source_commit_invalid")
    captured_at = _timestamp(
        document.get("captured_at_utc"), category="evidence_capture_timestamp_invalid"
    )
    workspace_fingerprint = _fingerprint(
        document.get("workspace_fingerprint"), "workspace_fingerprint_invalid"
    )

    identities = _exact_mapping(
        document.get("identities"), IDENTITY_NAMES, "evidence_identities_shape_invalid"
    )
    identity_fingerprints: dict[str, str] = {}
    for identity_name in sorted(IDENTITY_NAMES):
        identity = _exact_mapping(
            identities.get(identity_name),
            {"principal_fingerprint"},
            "evidence_identity_shape_invalid",
        )
        identity_fingerprints[identity_name] = _fingerprint(
            identity.get("principal_fingerprint"),
            f"{identity_name}_principal_fingerprint_invalid",
        )

    observations = document.get("observations")
    if (
        not isinstance(observations, list)
        or not observations
        or len(observations) > MAX_OBSERVATIONS
    ):
        raise VerificationError("evidence_observations_shape_invalid")

    normalized_observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for observation in observations:
        item = _exact_mapping(
            observation,
            {
                "evidence_id",
                "identity",
                "capabilities",
                "expectation",
                "outcome",
                "method",
                "observed_at_utc",
                "evidence_sha256",
            },
            "observation_shape_invalid",
        )
        evidence_id = _string(
            item.get("evidence_id"), category="observation_evidence_id_invalid", maximum_bytes=128
        )
        _expect(bool(_EVIDENCE_ID.fullmatch(evidence_id)), "observation_evidence_id_invalid")
        _expect(evidence_id not in seen, "observation_evidence_id_duplicate")
        seen.add(evidence_id)
        identity = _string(item.get("identity"), category="observation_identity_invalid")
        _expect(identity in IDENTITY_NAMES, "observation_identity_invalid")
        capabilities = _string_list(
            item.get("capabilities"),
            category="observation_capabilities_invalid",
            maximum_items=16,
        )
        expectation = _string(
            item.get("expectation"), category="observation_expectation_invalid"
        )
        _expect(expectation in ALLOWED_EXPECTATIONS, "observation_expectation_invalid")
        outcome = _string(item.get("outcome"), category="observation_outcome_invalid")
        _expect(outcome in ALLOWED_OUTCOMES, "observation_outcome_invalid")
        method = _string(item.get("method"), category="observation_method_invalid")
        _expect(method in ALLOWED_METHODS, "observation_method_invalid")
        observed_at = _timestamp(
            item.get("observed_at_utc"), category="observation_timestamp_invalid"
        )
        evidence_sha256 = _fingerprint(
            item.get("evidence_sha256"), "observation_evidence_digest_invalid"
        )
        normalized_observations.append(
            {
                "evidence_id": evidence_id,
                "identity": identity,
                "capabilities": capabilities,
                "expectation": expectation,
                "outcome": outcome,
                "method": method,
                "observed_at_utc": observed_at,
                "observed_at_text": item["observed_at_utc"],
                "evidence_sha256": evidence_sha256,
            }
        )

    return {
        "target": "dev",
        "repository": EXPECTED_REPOSITORY,
        "source_commit": source_commit,
        "captured_at_utc": captured_at,
        "captured_at_text": document["captured_at_utc"],
        "workspace_fingerprint": workspace_fingerprint,
        "identity_fingerprints": identity_fingerprints,
        "observations": normalized_observations,
    }


def _finding(category: str, evidence_id: str | None = None) -> dict[str, str]:
    finding = {"category": category}
    if evidence_id is not None:
        finding["evidence_id"] = evidence_id
    return finding


def _validate_observation_against_contract(
    observation: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str | None:
    identity = contract["identities"][observation["identity"]]
    expected = identity["allowed"] if observation["expectation"] == "allowed" else identity["denied"]
    if not set(observation["capabilities"]) <= expected:
        return "observation_capability_expectation_mismatch"
    return None


def _verify_semantics(
    contract: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    now: datetime,
    max_age_hours: float,
) -> tuple[list[dict[str, str]], int]:
    findings: list[dict[str, str]] = []
    oldest_allowed = now - timedelta(hours=max_age_hours)
    captured_at = evidence["captured_at_utc"]
    global_context_valid = True

    if len(set(evidence["identity_fingerprints"].values())) != len(IDENTITY_NAMES):
        findings.append(_finding("identity_fingerprints_overlap"))
        global_context_valid = False
    if captured_at > now + FUTURE_TOLERANCE:
        findings.append(_finding("evidence_capture_is_in_future"))
        global_context_valid = False
    if captured_at < oldest_allowed:
        findings.append(_finding("evidence_capture_is_stale"))
        global_context_valid = False

    by_id = {item["evidence_id"]: item for item in evidence["observations"]}
    verified_required = 0
    for required_id in contract["required"]:
        observation = by_id.get(required_id)
        if observation is None:
            findings.append(_finding("required_evidence_missing", required_id))
            continue
        rule = REQUIRED_EVIDENCE_RULES[required_id]
        if (
            observation["identity"] != rule["identity"]
            or tuple(observation["capabilities"]) != tuple(rule["capabilities"])
            or observation["expectation"] != rule["expectation"]
            or observation["method"] not in rule["methods"]
        ):
            findings.append(_finding("required_evidence_contract_mismatch", required_id))
            continue
        if observation["expectation"] == "allowed":
            if observation["outcome"] != "succeeded":
                findings.append(_finding("required_capability_not_succeeded", required_id))
                continue
        elif observation["outcome"] != "denied":
            findings.append(_finding("expected_denial_not_observed", required_id))
            continue
        observation_time_valid = (
            oldest_allowed <= observation["observed_at_utc"] <= now + FUTURE_TOLERANCE
            and observation["observed_at_utc"] <= captured_at + FUTURE_TOLERANCE
        )
        if global_context_valid and observation_time_valid:
            verified_required += 1

    for observation in evidence["observations"]:
        evidence_id = observation["evidence_id"]
        mismatch = _validate_observation_against_contract(observation, contract)
        if mismatch is not None:
            findings.append(_finding(mismatch, evidence_id))
        if observation["observed_at_utc"] > now + FUTURE_TOLERANCE:
            findings.append(_finding("observation_is_in_future", evidence_id))
        if observation["observed_at_utc"] < oldest_allowed:
            findings.append(_finding("observation_is_stale", evidence_id))
        if observation["observed_at_utc"] > captured_at + FUTURE_TOLERANCE:
            findings.append(_finding("observation_after_capture", evidence_id))

        if evidence_id not in REQUIRED_EVIDENCE_RULES:
            if observation["outcome"] == "error":
                findings.append(_finding("observation_error", evidence_id))
            elif observation["outcome"] == "not_tested":
                findings.append(_finding("observation_not_tested", evidence_id))
            elif (
                observation["expectation"] == "allowed"
                and observation["outcome"] != "succeeded"
            ):
                findings.append(_finding("allowed_capability_not_succeeded", evidence_id))
            elif (
                observation["expectation"] == "denied"
                and observation["outcome"] != "denied"
            ):
                findings.append(
                    _finding("denied_capability_unexpectedly_succeeded", evidence_id)
                )

    deduplicated = {
        (finding["category"], finding.get("evidence_id", "")): finding
        for finding in findings
    }
    ordered = [deduplicated[key] for key in sorted(deduplicated)]
    if len(ordered) > MAX_FINDINGS:
        ordered = ordered[: MAX_FINDINGS - 1]
        ordered.append(_finding("findings_truncated"))
    return ordered, verified_required


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verify_evidence(
    contract_path: Path,
    evidence_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise VerificationError("max_age_hours_invalid")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise VerificationError("verification_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)

    contract_bytes = _regular_bytes(contract_path, category="contract_file_invalid")
    evidence_bytes = _regular_bytes(evidence_path, category="evidence_file_invalid")
    contract = _validate_contract(_parse_json_object(contract_bytes, category="contract"))
    evidence = _validate_evidence_shape(
        _parse_json_object(evidence_bytes, category="evidence")
    )
    findings, verified_required = _verify_semantics(
        contract,
        evidence,
        now=reference_time,
        max_age_hours=max_age_hours,
    )
    observations = [
        {
            "evidence_id": item["evidence_id"],
            "identity": item["identity"],
            "capabilities": list(item["capabilities"]),
            "expectation": item["expectation"],
            "outcome": item["outcome"],
            "method": item["method"],
            "observed_at_utc": item["observed_at_text"],
            "evidence_sha256": item["evidence_sha256"],
        }
        for item in sorted(evidence["observations"], key=lambda item: item["evidence_id"])
    ]
    required_observation_ids = {item["evidence_id"] for item in evidence["observations"]}
    status = "verified" if not findings else "blocked"
    return {
        "schema_version": 1,
        "status": status,
        "generated_at_utc": _render_timestamp(reference_time),
        "target": evidence["target"],
        "repository": evidence["repository"],
        "source_commit": evidence["source_commit"],
        "captured_at_utc": evidence["captured_at_text"],
        "contract_sha256": _sha256(contract_bytes),
        "evidence_sha256": _sha256(evidence_bytes),
        "workspace_fingerprint": evidence["workspace_fingerprint"],
        "identity_fingerprints": evidence["identity_fingerprints"],
        "max_age_hours": max_age_hours,
        "required_evidence": {
            "required": len(contract["required"]),
            "observed": sum(
                1 for required_id in contract["required"] if required_id in required_observation_ids
            ),
            "verified": verified_required,
        },
        "observation_count": len(observations),
        "observations": observations,
        "findings": findings,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    required = report["required_evidence"]
    lines = [
        "# Identity privilege evidence verification",
        "",
        f"- Status: **{report['status']}**",
        f"- Target: `{report['target']}`",
        f"- Repository: `{report['repository']}`",
        f"- Source commit: `{report['source_commit']}`",
        f"- Captured: `{report['captured_at_utc']}`",
        f"- Contract: `{report['contract_sha256']}`",
        f"- Evidence manifest: `{report['evidence_sha256']}`",
        (
            "- Required evidence: "
            f"`{required['verified']}/{required['required']}` verified "
            f"(`{required['observed']}` observed)"
        ),
        "",
        "## Findings",
        "",
    ]
    if report["findings"]:
        for finding in report["findings"]:
            suffix = (
                f" (`{finding['evidence_id']}`)" if finding.get("evidence_id") else ""
            )
            lines.append(f"- `{finding['category']}`{suffix}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Observations",
            "",
            "| Evidence | Identity | Expectation | Outcome | Method | Capabilities |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for observation in report["observations"]:
        capabilities = ", ".join(f"`{item}`" for item in observation["capabilities"])
        lines.append(
            f"| `{observation['evidence_id']}` | `{observation['identity']}` | "
            f"`{observation['expectation']}` | `{observation['outcome']}` | "
            f"`{observation['method']}` | {capabilities} |"
        )
    lines.extend(
        [
            "",
            "Evidence contains fingerprints and digests, not raw principal identifiers, "
            "credentials, provider responses, table values, or workspace URLs.",
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
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--max-age-hours",
        type=positive_hours,
        default=DEFAULT_MAX_AGE_HOURS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = verify_evidence(
            args.contract,
            args.evidence,
            max_age_hours=args.max_age_hours,
        )
        write_outputs(args.output_dir, report)
    except VerificationError as error:
        print(
            f"Identity privilege evidence verification failed: {error.category}",
            file=sys.stderr,
        )
        return 2
    print(
        "Identity privilege evidence verification "
        f"{report['status']}: required="
        f"{report['required_evidence']['verified']}/"
        f"{report['required_evidence']['required']}"
    )
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
