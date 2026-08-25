#!/usr/bin/env python3
"""Verify one sanitized development alert-delivery evidence manifest."""
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

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = REPO_ROOT / "governance" / "operational_alert_policy.json"
EXPECTED_REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
OUTPUT_JSON = "alert-delivery-verification.json"
OUTPUT_MARKDOWN = "alert-delivery-verification.md"
DEFAULT_MAX_AGE_HOURS = 72.0
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_INPUT_BYTES = 1_000_000
MAX_STRING_BYTES = 512
MAX_FINDINGS = 64
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}\Z")
_REQUIRED_EXTERNAL_EVIDENCE = {
    "deployed_query_or_dashboard_identifier",
    "notification_destination_identifier",
    "test_alert_delivery_timestamp",
    "acknowledging_owner",
    "resolved_runbook_link",
}


class AlertEvidenceError(RuntimeError):
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
        raise AlertEvidenceError(category)


def _read_regular_bytes(path: Path, *, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise AlertEvidenceError(category)
        size = path.stat().st_size
        if size < 1 or size > MAX_INPUT_BYTES:
            raise AlertEvidenceError(f"{category}_size_invalid")
        value = path.read_bytes()
    except AlertEvidenceError:
        raise
    except OSError:
        raise AlertEvidenceError(f"{category}_unreadable") from None
    if len(value) > MAX_INPUT_BYTES:
        raise AlertEvidenceError(f"{category}_size_invalid")
    return value


def _parse_object(payload: bytes, *, category: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AlertEvidenceError(f"{category}_invalid_json") from None
    if not isinstance(value, dict):
        raise AlertEvidenceError(f"{category}_shape_invalid")
    return value


def _exact_mapping(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AlertEvidenceError(category)
    return value


def _string(value: Any, *, category: str, maximum: int = MAX_STRING_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise AlertEvidenceError(category)
    return value


def _fingerprint(value: Any, category: str) -> str:
    text = _string(value, category=category, maximum=71)
    if not _SHA256.fullmatch(text):
        raise AlertEvidenceError(category)
    return text


def _timestamp(value: Any, category: str) -> datetime:
    text = _string(value, category=category, maximum=64)
    if not text.endswith("Z"):
        raise AlertEvidenceError(category)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise AlertEvidenceError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AlertEvidenceError(category)
    return parsed.astimezone(timezone.utc)


def _bounded_int(value: Any, *, category: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise AlertEvidenceError(category)
    return value


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    raw = _read_regular_bytes(path, category="alert_policy_file_invalid")
    document = _parse_object(raw, category="alert_policy")
    _exact_mapping(
        document,
        {"schema_version", "owners", "retention_expectations", "alerts", "delivery"},
        "alert_policy_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "alert_policy_version_mismatch")
    owners = document.get("owners")
    if not isinstance(owners, dict) or not owners:
        raise AlertEvidenceError("alert_policy_owners_invalid")
    alerts = document.get("alerts")
    if not isinstance(alerts, list) or not alerts or len(alerts) > 50:
        raise AlertEvidenceError("alert_policy_alerts_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_alert in alerts:
        alert = _exact_mapping(
            raw_alert,
            {
                "id",
                "severity",
                "owner",
                "condition",
                "maximum_detection_delay_minutes",
                "runbook",
            },
            "alert_policy_alert_shape_invalid",
        )
        alert_id = _string(alert.get("id"), category="alert_policy_alert_id_invalid")
        _expect(alert_id not in normalized, "alert_policy_alert_id_duplicate")
        owner = _string(alert.get("owner"), category="alert_policy_owner_invalid")
        _expect(owner in owners, "alert_policy_owner_unknown")
        severity = _string(
            alert.get("severity"), category="alert_policy_severity_invalid"
        )
        _expect(severity in {"warning", "critical"}, "alert_policy_severity_invalid")
        runbook = _string(alert.get("runbook"), category="alert_policy_runbook_invalid")
        _expect(runbook.startswith("docs/"), "alert_policy_runbook_invalid")
        normalized[alert_id] = {
            "severity": severity,
            "owner": owner,
            "runbook": runbook,
            "maximum_detection_delay_minutes": _bounded_int(
                alert.get("maximum_detection_delay_minutes"),
                category="alert_policy_detection_delay_invalid",
                minimum=1,
                maximum=1_440,
            ),
        }
    delivery = document.get("delivery")
    if not isinstance(delivery, dict):
        raise AlertEvidenceError("alert_policy_delivery_invalid")
    external = delivery.get("required_external_evidence")
    if not isinstance(external, list) or set(external) != _REQUIRED_EXTERNAL_EVIDENCE:
        raise AlertEvidenceError("alert_policy_external_evidence_invalid")
    return {"raw_sha256": _sha256(raw), "alerts": normalized}


def _load_evidence(path: Path) -> dict[str, Any]:
    raw = _read_regular_bytes(path, category="alert_evidence_file_invalid")
    document = _parse_object(raw, category="alert_evidence")
    _exact_mapping(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "source_commit",
            "captured_at_utc",
            "workspace_fingerprint",
            "alert_event_id",
            "alert_id",
            "severity",
            "owner",
            "deployed_asset_fingerprint",
            "destination_fingerprint",
            "triggered_at_utc",
            "delivered_at_utc",
            "acknowledged_at_utc",
            "resolved_at_utc",
            "delivery_attempts",
            "notification_count",
            "delivery_status",
            "acknowledging_owner",
            "runbook",
            "test_alert",
            "evidence_sha256",
        },
        "alert_evidence_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "alert_evidence_version_mismatch")
    _expect(document.get("target") == "dev", "alert_evidence_target_must_be_dev")
    _expect(
        document.get("repository" == EXPECTED_REPOSITORY,
        "alert_evidence_repository_mismatch",
    )
    source_commit = _string(
        document.get("source_commit"),
        category="alert_evidence_source_commit_invalid",
        maximum=40,
    )
    _expect(bool(_COMMIT.fullmatch(source_commit)), "alert_evidence_source_commit_invalid")
    event_id = _string(
        document.get("alert_event_id"),
        category="alert_evidence_event_id_invalid",
        maximum=128,
    )
    _expect(bool(_EVENT_ID.fullmatch(event_id)), "alert_evidence_event_id_invalid")
    _expect(document.get("test_alert") is True, "alert_evidence_must_be_test_alert")
    return {
        "raw_sha256": _sha256(raw),
        "source_commit": source_commit,
        "captured_at": _timestamp(document.get("captured_at_utc"), "alert_evidence_capture_timestamp_invalid"),
        "captured_at_text": document["captured_at_utc"],
        "workspace_fingerprint": _fingerprint(document.get("workspace_fingerprint"), "alert_evidence_workspace_fingerprint_invalid"),
        "alert_event_id": event_id,
        "alert_id": _string(document.get("alert_id"), category="alert_evidence_alert_id_invalid"),
        "severity": _string(document.get("severity"), category="alert_evidence_severity_invalid"),
        "owner": _string(document.get("owner"), category="alert_evidence_owner_invalid"),
        "deployed_asset_fingerprint": _fingerprint(document.get("deployed_asset_fingerprint"), "alert_evidence_deployed_asset_fingerprint_invalid"),
        "destination_fingerprint": _fingerprint(document.get("destination_fingerprint"), "alert_evidence_destination_fingerprint_invalid"),
        "triggered_at": _timestamp(document.get("triggered_at_utc"), "alert_evidence_triggered_timestamp_invalid"),
        "triggered_at_text": document["triggered_at_utc"],
        "delivered_at": _timestamp(document.get("delivered_at_utc"), "alert_evidence_delivered_timestamp_invalid"),
        "delivered_at_text": document["delivered_at_utc"],
        "acknowledged_at": _timestamp(document.get("acknowledged_at_utc"), "alert_evidence_acknowledged_timestamp_invalid"),
        "acknowledged_at_text": document["acknowledged_at_utc"],
        "resolved_at": _timestamp(document.get("resolved_at_utc"), "alert_evidence_resolved_timestamp_invalid"),
        "resolved_at_text": document["resolved_at_utc"],
        "delivery_attempts": _bounded_int(document.get("delivery_attempts"), category="alert_evidence_delivery_attempts_invalid", minimum=1, maximum=5),
        "notification_count": _bounded_int(document.get("notification_count"), category="alert_evidence_notification_count_invalid", minimum=0, maximum=10,),
        "delivery_status": _string(document.get("delivery_status"), category="alert_evidence_delivery_status_invalid"),
        "acknowledging_owner": _string(document.get("acknowledging_owner"), category="alert_evidence_acknowledging_owner_invalid"),
        "runbook": _string(document.get("runbook"), category="alert_evidence_runbook_invalid"),
        "evidence_sha256": _fingerprint(document.get("evidence_sha256"), "alert_evidence_protected_evidence_digest_invalid"),
    }


def _finding(category: str) -> dict[str, str]:
    return {"category": category}


def verify_evidence(
    policy_path: Path,
    evidence_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    repository_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise AlertEvidenceError("alert_max_age_hours_invalid")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise AlertEvidenceError("alert_verification_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)
    policy = load_policy(policy_path)
    evidence = _load_evidence(evidence_path)
    alert = policy["alerts"].get(evidence["alert_id"])
    if alert is None:
        raise AlertEvidenceError("alert_evidence_alert_id_unknown")
    findings: list[dict[str, str]] = []
    if evidence["captured_at"] < reference_time - timedelta(hours=max_age_hours):
        findings.append(_finding("alert_evidence_capture_is_stale"))
    if evidence["captured_at"] > reference_time + FUTURE_TOLERANCE:
        findings.append(_finding("alert_evidence_capture_is_in_future"))
    ordered_times = [
        evidence["triggered_at"],
        evidence["delivered_at"],
        evidence["acknowledged_at"],
        evidence["resolved_at"],
        evidence["captured_at"],
    ]
    if ordered_times != sorted(ordered_times):
        findings.append(_finding("alert_evidence_timestamps_out_of_order"))
    if any(value > reference_time + FUTURE_TOLERANCE for value in ordered_times):
        findings.append(_finding("alert_evidence_timestamp_is_in_future"))
    delivery_delay = (evidence["delivered_at"] - evidence["triggered_at"]).total_seconds() / 60
    if delivery_delay > alert["maximum_detection_delay_minutes"]:
        findings.append(_finding("alert_delivery_delay_exceeds_policy")
    if evidence["severity"] != alert["severity"]:
        findings.append(_finding("alert_evidence_severity_mismatch"))
    if evidence["owner"] != alert["owner"]:
        findings.append(_finding("alert_evidence_owner_mismatch"))
    if evidence["acknowledging_owner"] != alert["owner"]:
        findings.append(_finding("alert_evidence_acknowledging_owner_mismatch")
    if evidence["runbook"] != alert["runbook"]:
        findings.append(_finding("alert_evidence_runbook_mismatch")
    runbook_path = repository_root / alert["runbook"].split("#", 1)[0]
    if runbook_path.is_symlink() or not runbook_path.is_file():
        findings.append(_finding("alert_evidence_runbook_unresolved")
    if evidence["delivery_status"] != "delivered":
        findings.append(_finding("alert_delivery_not_confirmed")
    if evidence["notification_count"] != 1:
        findings.append(_finding("alert_notification_count_unexpected"))
    if evidence["deployed_asset_fingerprint"] == evidence["destination_fingerprint"]:
        findings.append(_finding("alert_asset_and_destination_fingerprints_overlap")
    if len(findings) > MAX_FINDINGS:
        findings = findings[: MAX_FINDINGS - 1] + [_finding("alert_findings_truncated")]
    status = "verified" if not findings else "blocked"
    return {
        "schema_version": 1,
        "status": status,
        "generated_at_utc": _utc_text(reference_time),
        "target": "dev",
        "repository": EXPECTED_REPOSITORY,
        "source_commit": evidence["source_commit"],
        "captured_at_utc": evidence["captured_at_text"],
        "policy_sha256": policy["raw_sha256"],
        "evidence_manifest_sha256": evidence["raw_sha256"],
        "protected_evidence_sha256": evidence["evidence_sha256"],
        "workspace_fingerprint": evidence["workspace_fingerprint"],
        "deployed_asset_fingerprint": evidence["deployed_asset_fingerprint"],
        "destination_fingerprint": evidence["destination_fingerprint"],
        "alert_event_id": evidence["alert_event_id"],
        "alert_id": evidence["alert_id"],
        "severity": evidence["severity"],
        "owner": evidence["owner"],
        "runbook": evidence["runbook"],
        "triggered_at_utc": evidence["triggered_at_text"],
        "delivered_at_utc": evidence["delivered_at_text"],
        "acknowledged_at_utc": evidence["acknowledged_at_text"],
        "resolved_at_utc": evidence["resolved_at_text"],
        "delivery_delay_minutes": delivery_delay,
        "maximum_detection_delay_minutes": alert["maximum_detection_delay_minutes"],
        "delivery_attempts": evidence["delivery_attempts"],
        "notification_count": evidence["notification_count"],
        "delivery_status": evidence["delivery_status"],
        "acknowledging_owner": evidence["acknowledging_owner"],
        "findings": sorted(findings, key=lambda item: item["category"]),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Alert delivery evidence verification",
        "",
        f"- Status: **{report['status']}**",
        f"- Alert: `{report['alert_id']}`",
        f"- Severity: `{report['severity']}`",
        f"- Owner: `{report['owner']}`",
        f"- Source commit: `{report['source_commit']}`",
        f"- Delivery delay: `{report['delivery_delay_minutes']}` minutes",
        f"- Maximum delay: `{report['maximum_detection_delay_minutes']}` minutes",
        f"- Notifications: `{report['notification_count']}`",
        f"- Attempts: `{report['delivery_attempts']}`",
        "",
        "## Findings",
        "",
    ]
    if report["findings"]:
        lines.extend(f"- `{item['category']}`" for item in report["findings"])
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "The report contains fingerprints and evidence digests, not destination URLs, "
            "credentials, provider responses, raw telemetry rows or notification bodies.",
            "",
        ]
    )
    return "\n".join(lines)


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise AlertEvidenceError("alert_output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise AlertEvidenceError("alert_output_directory_unavailable") from None
    if path.is_symlink() or not path.is_dir():
        raise AlertEvidenceError("alert_output_directory_invalid")
    return path


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise AlertEvidenceError("alert_temporary_output_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise AlertEvidenceError("alert_output_path_invalid")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AlertEvidenceError("alert_output_write_failed") from None


def write_outputs(output_directory: Path, report: Mapping[str, Any]) -> None:
    directory = _prepare_output_directory(output_directory)
    _write_atomic(
        directory / OUTPUT_JSON,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    _write_atomic(directory / OUTPUT_MARKDOWN, render_markdown(report))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--max-age-hours", type=positive_hours, default=DEFAULT_MAX_AGE_HOURS
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = verify_evidence(
            args.policy,
            args.evidence,
            max_age_hours=args.max_age_hours,
        )
        write_outputs(args.output_dir, report)
    except AlertEvidenceError as error:
        print(f"Alert delivery evidence verification failed: {error.category}", file=sys.stderr)
        return 2
    print(
        f"Alert delivery evidence {report['status']}: alert={report['alert_id']}"
    )
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
