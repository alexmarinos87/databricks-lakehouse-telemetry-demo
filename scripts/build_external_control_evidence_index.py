#!/usr/bin/env python3
"""Build one freshness-bounded index of effective external-control evidence."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_POLICY = REPO_ROOT / "governance" / "external_control_evidence_policy.json"
OUTPUT_JSON = "external-control-evidence-index.json"
OUTPUT_MARKDOWN = "external-control-evidence-index.md"
MAX_POLICY_BYTES = 100_000
MAX_METADATA_BYTES = 500_000
MAX_REPORT_BYTES = 2_000_000
MAX_VERIFIER_BYTES = 1_000_000
FUTURE_TOLERANCE = timedelta(minutes=5)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CONTROL_ID = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
EXPECTED_CONTROLS = (
    "github_governance",
    "databricks_federation",
    "identity_privilege",
)

_SPEC = importlib.util.spec_from_file_location(
    "_external_control_evidence_io", HERE / "protected_evidence_io.py"
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("protected_evidence_io_unavailable")
io = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = io
_SPEC.loader.exec_module(io)


class IndexError(RuntimeError):
    """Stable invalid-input category safe to expose in logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _translate(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except io.EvidenceIOError as error:
        raise IndexError(error.category) from None


def _expect(condition: bool, category: str) -> None:
    if not condition:
        raise IndexError(category)


def _exact(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise IndexError(category)
    return value


def _text(value: Any, category: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise IndexError(category)
    return value


def _timestamp(value: Any, category: str) -> datetime:
    text = _text(value, category, 64)
    if not text.endswith("Z"):
        raise IndexError(category)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise IndexError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise IndexError(category)
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_number(value: Any, category: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndexError(category)
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0 or parsed > maximum:
        raise IndexError(category)
    return parsed


def _control_id(value: Any, category: str) -> str:
    text = _text(value, category, 64)
    if not _CONTROL_ID.fullmatch(text):
        raise IndexError(category)
    return text


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    raw = _translate(
        io.regular_bytes,
        path,
        maximum=MAX_POLICY_BYTES,
        category="external_control_policy",
    )
    document = _translate(io.json_object, raw, "external_control_policy")
    _exact(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "maximum_evidence_age_hours",
            "maximum_capture_spread_hours",
            "controls",
        },
        "external_control_policy_shape_invalid",
    )
    _expect(
        document.get("schema_version") == 1,
        "external_control_policy_version_mismatch",
    )
    _expect(document.get("target") == "dev", "external_control_policy_target_invalid")
    repository = _text(
        document.get("repository"),
        "external_control_policy_repository_invalid",
        256,
    )
    maximum_age = _positive_number(
        document.get("maximum_evidence_age_hours"),
        "external_control_policy_maximum_age_invalid",
        720,
    )
    maximum_spread = _positive_number(
        document.get("maximum_capture_spread_hours"),
        "external_control_policy_capture_spread_invalid",
        72,
    )
    controls = document.get("controls")
    if not isinstance(controls, list) or len(controls) != len(EXPECTED_CONTROLS):
        raise IndexError("external_control_policy_controls_invalid")
    normalized: list[dict[str, str]] = []
    for expected_id, raw_control in zip(EXPECTED_CONTROLS, controls, strict=True):
        control = _exact(
            raw_control,
            {"control_id", "verifier_path"},
            "external_control_policy_control_shape_invalid",
        )
        control_id = _control_id(
            control.get("control_id"),
            "external_control_policy_control_id_invalid",
        )
        _expect(
            control_id == expected_id,
            "external_control_policy_control_order_invalid",
        )
        verifier_path = _translate(
            io.canonical_relative,
            control.get("verifier_path"),
            invalid="external_control_policy_verifier_path_invalid",
            noncanonical="external_control_policy_verifier_path_not_canonical",
        )
        _expect(
            verifier_path.startswith("scripts/"),
            "external_control_policy_verifier_path_invalid",
        )
        normalized.append(
            {"control_id": control_id, "verifier_path": verifier_path}
        )
    return {
        "raw_sha256": io.sha256(raw),
        "target": "dev",
        "repository": repository,
        "maximum_age_hours": maximum_age,
        "maximum_spread_hours": maximum_spread,
        "controls": normalized,
    }


def load_metadata(path: Path, policy: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    raw = _translate(
        io.regular_bytes,
        path,
        maximum=MAX_METADATA_BYTES,
        category="external_control_metadata",
    )
    document = _translate(io.json_object, raw, "external_control_metadata")
    _exact(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "source_commit",
            "captured_at_utc",
            "controls",
        },
        "external_control_metadata_shape_invalid",
    )
    _expect(
        document.get("schema_version") == 1,
        "external_control_metadata_version_mismatch",
    )
    _expect(
        document.get("target") == policy["target"],
        "external_control_metadata_target_mismatch",
    )
    _expect(
        document.get("repository") == policy["repository"],
        "external_control_metadata_repository_mismatch",
    )
    source_commit = _text(
        document.get("source_commit"),
        "external_control_metadata_source_commit_invalid",
        40,
    )
    _expect(
        bool(_COMMIT.fullmatch(source_commit)),
        "external_control_metadata_source_commit_invalid",
    )
    captured_at = _timestamp(
        document.get("captured_at_utc"),
        "external_control_metadata_capture_timestamp_invalid",
    )
    controls = document.get("controls")
    if not isinstance(controls, list) or len(controls) != len(policy["controls"]):
        raise IndexError("external_control_metadata_controls_invalid")

    normalized: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    seen_report_digests: set[str] = set()
    for expected, raw_control in zip(policy["controls"], controls, strict=True):
        control = _exact(
            raw_control,
            {
                "control_id",
                "report_path",
                "expected_report_sha256",
                "expected_verifier_sha256",
                "workflow_run_fingerprint",
            },
            "external_control_metadata_control_shape_invalid",
        )
        control_id = _control_id(
            control.get("control_id"),
            "external_control_metadata_control_id_invalid",
        )
        _expect(
            control_id == expected["control_id"],
            "external_control_metadata_control_order_invalid",
        )
        report_path = _translate(
            io.canonical_relative,
            control.get("report_path"),
            invalid="external_control_report_path_invalid",
            noncanonical="external_control_report_path_not_canonical",
        )
        report_digest = _translate(
            io.fingerprint,
            control.get("expected_report_sha256"),
            "external_control_report_expected_digest_invalid",
        )
        verifier_digest = _translate(
            io.fingerprint,
            control.get("expected_verifier_sha256"),
            "external_control_verifier_expected_digest_invalid",
        )
        workflow_fingerprint = _translate(
            io.fingerprint,
            control.get("workflow_run_fingerprint"),
            "external_control_workflow_fingerprint_invalid",
        )
        _expect(
            report_path not in seen_paths,
            "external_control_report_path_duplicate",
        )
        _expect(
            report_digest not in seen_report_digests,
            "external_control_report_digest_duplicate",
        )
        seen_paths.add(report_path)
        seen_report_digests.add(report_digest)
        normalized.append(
            {
                "control_id": control_id,
                "report_path": report_path,
                "expected_report_sha256": report_digest,
                "expected_verifier_sha256": verifier_digest,
                "workflow_run_fingerprint": workflow_fingerprint,
                "verifier_path": expected["verifier_path"],
            }
        )
    return {
        "target": policy["target"],
        "repository": policy["repository"],
        "source_commit": source_commit,
        "captured_at": captured_at,
        "captured_at_text": document["captured_at_utc"],
        "controls": normalized,
    }, raw


def _finding(category: str, control_id: str | None = None) -> dict[str, str]:
    finding = {"category": category}
    if control_id is not None:
        finding["control_id"] = control_id
    return finding


def _verified_report_common(
    report: Mapping[str, Any],
    *,
    control_id: str,
    repository: str,
) -> tuple[datetime, list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    if report.get("schema_version") != 1:
        findings.append(_finding("control_report_schema_version_mismatch", control_id))
    if report.get("status") != "verified":
        findings.append(_finding("control_report_is_not_verified", control_id))
    if report.get("repository") != repository:
        findings.append(_finding("control_report_repository_mismatch", control_id))
    report_findings = report.get("findings")
    if not isinstance(report_findings, list) or report_findings:
        findings.append(_finding("control_report_contains_findings", control_id))
    generated_at = _timestamp(
        report.get("generated_at_utc"),
        "control_report_generated_timestamp_invalid",
    )
    return generated_at, findings


def _github_findings(
    report: Mapping[str, Any],
    *,
    source_commit: str,
) -> list[dict[str, str]]:
    control_id = "github_governance"
    findings: list[dict[str, str]] = []
    if report.get("branch") != "main":
        findings.append(_finding("github_report_branch_mismatch", control_id))
    if report.get("branch_head_sha") != source_commit:
        findings.append(_finding("github_report_source_commit_mismatch", control_id))
    if report.get("main_protected") is not True:
        findings.append(_finding("github_report_main_not_protected", control_id))
    protection = report.get("branch_protection")
    required = {
        "required_checks_strict",
        "validate_required",
        "administrator_enforcement",
        "linear_history",
        "force_pushes_blocked",
        "deletion_blocked",
        "conversation_resolution",
        "dismiss_stale_reviews",
    }
    if not isinstance(protection, dict) or any(
        protection.get(name) is not True for name in required
    ):
        findings.append(_finding("github_report_protection_incomplete", control_id))
    environments = report.get("environments")
    expected_names = {"dev-plan", "prod-plan", "dev", "prod"}
    if not isinstance(environments, list) or {
        item.get("environment")
        for item in environments
        if isinstance(item, dict)
    } != expected_names:
        findings.append(_finding("github_report_environment_coverage_mismatch", control_id))
    elif any(
        item.get("verified") is not True
        or item.get("custom_main_only_policy") is not True
        or item.get("static_client_secret_absent") is not True
        or not isinstance(item.get("variables"), dict)
        or not item["variables"]
        or not all(value is True for value in item["variables"].values())
        for item in environments
    ):
        findings.append(_finding("github_report_environment_not_verified", control_id))
    return findings


def _federation_findings(report: Mapping[str, Any]) -> list[dict[str, str]]:
    control_id = "databricks_federation"
    findings: list[dict[str, str]] = []
    if report.get("issuer") != "https://token.actions.githubusercontent.com":
        findings.append(_finding("federation_report_issuer_mismatch", control_id))
    principals = report.get("principals")
    if not isinstance(principals, list) or not principals or len(principals) > 8:
        findings.append(_finding("federation_report_principal_coverage_invalid", control_id))
        return findings
    for principal in principals:
        if not isinstance(principal, dict):
            findings.append(_finding("federation_report_principal_shape_invalid", control_id))
            break
        if any(
            principal.get(name) is not True
            for name in (
                "numeric_id_matches",
                "application_id_matches",
                "active",
                "account_admin_absent",
                "oauth_secrets_absent",
            )
        ):
            findings.append(_finding("federation_report_principal_not_verified", control_id))
            break
        policies = principal.get("policies")
        if not isinstance(policies, list) or not policies or any(
            not isinstance(policy, dict) or policy.get("exact_policy") is not True
            for policy in policies
        ):
            findings.append(_finding("federation_report_policy_not_verified", control_id))
            break
    return findings


def _identity_findings(
    report: Mapping[str, Any],
    *,
    source_commit: str,
) -> list[dict[str, str]]:
    control_id = "identity_privilege"
    findings: list[dict[str, str]] = []
    if report.get("target") != "dev":
        findings.append(_finding("identity_report_target_mismatch", control_id))
    if report.get("source_commit") != source_commit:
        findings.append(_finding("identity_report_source_commit_mismatch", control_id))
    required = report.get("required_evidence")
    if (
        not isinstance(required, dict)
        or isinstance(required.get("required"), bool)
        or not isinstance(required.get("required"), int)
        or required.get("required", 0) < 1
        or required.get("observed") != required.get("required")
        or required.get("verified") != required.get("required")
    ):
        findings.append(_finding("identity_report_required_evidence_incomplete", control_id))
    identity_fingerprints = report.get("identity_fingerprints")
    if (
        not isinstance(identity_fingerprints, dict)
        or set(identity_fingerprints) != {"deployment", "runtime"}
        or len(set(identity_fingerprints.values())) != 2
    ):
        findings.append(_finding("identity_report_identity_fingerprints_invalid", control_id))
    _timestamp(
        report.get("captured_at_utc"),
        "identity_report_capture_timestamp_invalid",
    )
    return findings


def build_index(
    policy_path: Path,
    metadata_path: Path,
    evidence_root: Path,
    repository_root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    metadata, metadata_bytes = load_metadata(metadata_path, policy)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() != timedelta(0):
        raise IndexError("external_control_verification_time_must_be_utc")
    reference = reference.astimezone(timezone.utc)

    findings: list[dict[str, str]] = []
    oldest = reference - timedelta(hours=policy["maximum_age_hours"])
    captured_at = metadata["captured_at"]
    if captured_at < oldest:
        findings.append(_finding("external_control_index_capture_is_stale"))
    if captured_at > reference + FUTURE_TOLERANCE:
        findings.append(_finding("external_control_index_capture_is_in_future"))

    control_results: list[dict[str, str]] = []
    report_times: list[datetime] = []
    for descriptor in metadata["controls"]:
        control_id = descriptor["control_id"]
        report_path = _translate(
            io.protected_path,
            evidence_root,
            descriptor["report_path"],
            prefix=f"external_control_{control_id}_report",
        )
        report_bytes = _translate(
            io.regular_bytes,
            report_path,
            maximum=MAX_REPORT_BYTES,
            category=f"external_control_{control_id}_report",
        )
        report_sha256 = io.sha256(report_bytes)
        _expect(
            report_sha256 == descriptor["expected_report_sha256"],
            "external_control_report_digest_mismatch",
        )
        report = _translate(
            io.json_object,
            report_bytes,
            f"external_control_{control_id}_report",
        )

        verifier_path = _translate(
            io.protected_path,
            repository_root,
            descriptor["verifier_path"],
            prefix=f"external_control_{control_id}_verifier",
        )
        verifier_bytes = _translate(
            io.regular_bytes,
            verifier_path,
            maximum=MAX_VERIFIER_BYTES,
            category=f"external_control_{control_id}_verifier",
        )
        verifier_sha256 = io.sha256(verifier_bytes)
        _expect(
            verifier_sha256 == descriptor["expected_verifier_sha256"],
            "external_control_verifier_digest_mismatch",
        )

        report_time, common_findings = _verified_report_common(
            report,
            control_id=control_id,
            repository=metadata["repository"],
        )
        findings.extend(common_findings)
        report_times.append(report_time)
        if report_time < oldest:
            findings.append(_finding("control_report_is_stale", control_id))
        if report_time > reference + FUTURE_TOLERANCE:
            findings.append(_finding("control_report_is_in_future", control_id))
        if report_time > captured_at + FUTURE_TOLERANCE:
            findings.append(_finding("control_report_after_index_capture", control_id))

        if control_id == "github_governance":
            findings.extend(
                _github_findings(report, source_commit=metadata["source_commit"])
            )
        elif control_id == "databricks_federation":
            findings.extend(_federation_findings(report))
        elif control_id == "identity_privilege":
            findings.extend(
                _identity_findings(report, source_commit=metadata["source_commit"])
            )
        else:
            raise IndexError("external_control_id_unsupported")

        control_results.append(
            {
                "control_id": control_id,
                "report_sha256": report_sha256,
                "verifier_sha256": verifier_sha256,
                "report_generated_at_utc": _utc(report_time),
                "workflow_run_fingerprint": descriptor[
                    "workflow_run_fingerprint"
                ],
            }
        )

    if report_times and max(report_times) - min(report_times) > timedelta(
        hours=policy["maximum_spread_hours"]
    ):
        findings.append(_finding("external_control_capture_spread_exceeds_policy"))

    deduplicated = {
        (item["category"], item.get("control_id", "")): item for item in findings
    }
    ordered_findings = [deduplicated[key] for key in sorted(deduplicated)]
    return {
        "schema_version": 1,
        "status": "verified" if not ordered_findings else "blocked",
        "generated_at_utc": _utc(reference),
        "target": metadata["target"],
        "repository": metadata["repository"],
        "source_commit": metadata["source_commit"],
        "captured_at_utc": metadata["captured_at_text"],
        "policy_sha256": policy["raw_sha256"],
        "metadata_sha256": io.sha256(metadata_bytes),
        "maximum_evidence_age_hours": policy["maximum_age_hours"],
        "maximum_capture_spread_hours": policy["maximum_spread_hours"],
        "external_mutation_authorized": False,
        "controls": control_results,
        "findings": ordered_findings,
    }


def render_markdown(index: Mapping[str, Any]) -> str:
    lines = [
        "# External control evidence index",
        "",
        f"- Status: **{index['status']}**",
        f"- Target: `{index['target']}`",
        f"- Repository: `{index['repository']}`",
        f"- Source commit: `{index['source_commit']}`",
        f"- Captured: `{index['captured_at_utc']}`",
        f"- External mutation authorized: `{str(index['external_mutation_authorized']).lower()}`",
        "",
        "## Controls",
        "",
        "| Control | Report digest | Verifier digest | Generated |",
        "| --- | --- | --- | --- |",
    ]
    for control in index["controls"]:
        lines.append(
            f"| `{control['control_id']}` | `{control['report_sha256']}` | "
            f"`{control['verifier_sha256']}` | "
            f"`{control['report_generated_at_utc']}` |"
        )
    lines.extend(["", "## Findings", ""])
    if index["findings"]:
        for finding in index["findings"]:
            suffix = (
                f" (`{finding['control_id']}`)"
                if finding.get("control_id")
                else ""
            )
            lines.append(f"- `{finding['category']}`{suffix}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "The index contains report and verifier digests, timestamps and workflow "
            "fingerprints. It excludes report paths, report bodies, workspace URLs, "
            "principal values, provider responses and credentials.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(output_directory: Path, index: Mapping[str, Any]) -> None:
    directory = _translate(
        io.prepare_output_directory,
        output_directory,
        "external_control_index_output",
    )
    _translate(
        io.write_atomic,
        directory / OUTPUT_JSON,
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        "external_control_index",
    )
    _translate(
        io.write_atomic,
        directory / OUTPUT_MARKDOWN,
        render_markdown(index),
        "external_control_index",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        index = build_index(
            args.policy,
            args.metadata,
            args.evidence_root,
            args.repository_root,
        )
        write_outputs(args.output_dir, index)
    except IndexError as error:
        print(
            f"External control evidence indexing failed: {error.category}",
            file=sys.stderr,
        )
        return 2
    print(
        f"External control evidence index {index['status']}: "
        f"controls={len(index['controls'])}"
    )
    return 0 if index["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
