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
MAX_FINDINGS = 64
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_EVENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}\Z")
_REQUIRED_EXTERNAL = {
    "deployed_query_or_dashboard_identifier",
    "notification_destination_identifier",
    "test_alert_delivery_timestamp",
    "acknowledging_owner",
    "resolved_runbook_link",
}
_EVIDENCE_KEYS = {
    "schema_version", "target", "repository", "source_commit", "captured_at_utc",
    "workspace_fingerprint", "alert_event_id", "alert_id", "severity", "owner",
    "deployed_asset_fingerprint", "destination_fingerprint", "triggered_at_utc",
    "delivered_at_utc", "acknowledged_at_utc", "resolved_at_utc",
    "delivery_attempts", "notification_count", "delivery_status",
    "acknowledging_owner", "runbook", "test_alert", "evidence_sha256",
}


class AlertEvidenceError(RuntimeError):
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


def _read(path: Path, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise AlertEvidenceError(category)
        if not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
            raise AlertEvidenceError(category + "_size_invalid")
        payload = path.read_bytes()
    except AlertEvidenceError:
        raise
    except OSError:
        raise AlertEvidenceError(category + "_unreadable") from None
    if len(payload) > MAX_INPUT_BYTES:
        raise AlertEvidenceError(category + "_size_invalid")
    return payload


def _object(payload: bytes, category: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AlertEvidenceError(category + "_invalid_json") from None
    if not isinstance(value, dict):
        raise AlertEvidenceError(category + "_shape_invalid")
    return value


def _exact(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AlertEvidenceError(category)
    return value


def _text(value: Any, category: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > maximum or any(c in value for c in "\0\n\r"):
        raise AlertEvidenceError(category)
    return value


def _fingerprint(value: Any, category: str) -> str:
    text = _text(value, category, 71)
    if not _SHA.fullmatch(text):
        raise AlertEvidenceError(category)
    return text


def _time(value: Any, category: str) -> datetime:
    text = _text(value, category, 64)
    if not text.endswith("Z"):
        raise AlertEvidenceError(category)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise AlertEvidenceError(category) from None
    if parsed.utcoffset() != timedelta(0):
        raise AlertEvidenceError(category)
    return parsed.astimezone(timezone.utc)


def _integer(value: Any, category: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AlertEvidenceError(category)
    return value


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    raw = _read(path, "alert_policy_file_invalid")
    document = _object(raw, "alert_policy")
    _exact(document, {"schema_version", "owners", "retention_expectations", "alerts", "delivery"}, "alert_policy_shape_invalid")
    _expect(document.get("schema_version") == 1, "alert_policy_version_mismatch")
    owners = document.get("owners")
    _expect(isinstance(owners, dict) and bool(owners), "alert_policy_owners_invalid")
    alerts = document.get("alerts")
    _expect(isinstance(alerts, list) and 0 < len(alerts) <= 50, "alert_policy_alerts_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    keys = {"id", "severity", "owner", "condition", "maximum_detection_delay_minutes", "runbook"}
    for raw_alert in alerts:
        alert = _exact(raw_alert, keys, "alert_policy_alert_shape_invalid")
        alert_id = _text(alert.get("id"), "alert_policy_alert_id_invalid")
        _expect(alert_id not in normalized, "alert_policy_alert_id_duplicate")
        owner = _text(alert.get("owner"), "alert_policy_owner_invalid")
        _expect(owner in owners, "alert_policy_owner_unknown")
        severity = _text(alert.get("severity"), "alert_policy_severity_invalid")
        _expect(severity in {"warning", "critical"}, "alert_policy_severity_invalid")
        runbook = _text(alert.get("runbook"), "alert_policy_runbook_invalid")
        _expect(runbook.startswith("docs/"), "alert_policy_runbook_invalid")
        normalized[alert_id] = {
            "severity": severity,
            "owner": owner,
            "runbook": runbook,
            "maximum_detection_delay_minutes": _integer(
                alert.get("maximum_detection_delay_minutes"),
                "alert_policy_detection_delay_invalid", 1, 1440,
            ),
        }
    delivery = document.get("delivery")
    _expect(isinstance(delivery, dict), "alert_policy_delivery_invalid")
    external = delivery.get("required_external_evidence")
    _expect(isinstance(external, list) and set(external) == _REQUIRED_EXTERNAL, "alert_policy_external_evidence_invalid")
    return {"raw_sha256": _digest(raw), "alerts": normalized}


def _load_evidence(path: Path) -> dict[str, Any]:
    raw = _read(path, "alert_evidence_file_invalid")
    doc = _object(raw, "alert_evidence")
    _exact(doc, _EVIDENCE_KEYS, "alert_evidence_shape_invalid")
    _expect(doc.get("schema_version") == 1, "alert_evidence_version_mismatch")
    _expect(doc.get("target") == "dev", "alert_evidence_target_must_be_dev")
    _expect(doc.get("repository") == EXPECTED_REPOSITORY, "alert_evidence_repository_mismatch")
    commit = _text(doc.get("source_commit"), "alert_evidence_source_commit_invalid", 40)
    _expect(bool(_COMMIT.fullmatch(commit)), "alert_evidence_source_commit_invalid")
    event_id = _text(doc.get("alert_event_id"), "alert_evidence_event_id_invalid", 128)
    _expect(bool(_EVENT.fullmatch(event_id)), "alert_evidence_event_id_invalid")
    _expect(doc.get("test_alert") is True, "alert_evidence_must_be_test_alert")
    return {
        "raw_sha256": _digest(raw), "source_commit": commit,
        "captured_at": _time(doc["captured_at_utc"], "alert_evidence_capture_timestamp_invalid"),
        "captured_at_text": doc["captured_at_utc"],
        "workspace_fingerprint": _fingerprint(doc["workspace_fingerprint"], "alert_evidence_workspace_fingerprint_invalid"),
        "alert_event_id": event_id,
        "alert_id": _text(doc["alert_id"], "alert_evidence_alert_id_invalid"),
        "severity": _text(doc["severity"], "alert_evidence_severity_invalid"),
        "owner": _text(doc["owner"], "alert_evidence_owner_invalid"),
        "deployed_asset_fingerprint": _fingerprint(doc["deployed_asset_fingerprint"], "alert_evidence_asset_fingerprint_invalid"),
        "destination_fingerprint": _fingerprint(doc["destination_fingerprint"], "alert_evidence_destination_fingerprint_invalid"),
        "triggered_at": _time(doc["triggered_at_utc"], "alert_evidence_triggered_timestamp_invalid"),
        "triggered_at_text": doc["triggered_at_utc"],
        "delivered_at": _time(doc["delivered_at_utc"], "alert_evidence_delivered_timestamp_invalid"),
        "delivered_at_text": doc["delivered_at_utc"],
        "acknowledged_at": _time(doc["acknowledged_at_utc"], "alert_evidence_acknowledged_timestamp_invalid"),
        "acknowledged_at_text": doc["acknowledged_at_utc"],
        "resolved_at": _time(doc["resolved_at_utc"], "alert_evidence_resolved_timestamp_invalid"),
        "resolved_at_text": doc["resolved_at_utc"],
        "delivery_attempts": _integer(doc["delivery_attempts"], "alert_evidence_delivery_attempts_invalid", 1, 5),
        "notification_count": _integer(doc["notification_count"], "alert_evidence_notification_count_invalid", 0, 10),
        "delivery_status": _text(doc["delivery_status"], "alert_evidence_delivery_status_invalid"),
        "acknowledging_owner": _text(doc["acknowledging_owner"], "alert_evidence_acknowledging_owner_invalid"),
        "runbook": _text(doc["runbook"], "alert_evidence_runbook_invalid"),
        "evidence_sha256": _fingerprint(doc["evidence_sha256"], "alert_evidence_digest_invalid"),
    }


def verify_evidence(policy_path: Path, evidence_path: Path, *, repository_root: Path = REPO_ROOT,
                    now: datetime | None = None, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> dict[str, Any]:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise AlertEvidenceError("alert_evidence_max_age_invalid")
    policy, evidence = load_policy(policy_path), _load_evidence(evidence_path)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() != timedelta(0):
        raise AlertEvidenceError("alert_evidence_verification_time_must_be_utc")
    reference = reference.astimezone(timezone.utc)
    alert = policy["alerts"].get(evidence["alert_id"])
    if alert is None:
        raise AlertEvidenceError("alert_evidence_alert_id_unknown")
    findings: list[dict[str, str]] = []
    add = lambda category: findings.append({"category": category})
    if evidence["captured_at"] < reference - timedelta(hours=max_age_hours): add("alert_evidence_capture_is_stale")
    if evidence["captured_at"] > reference + FUTURE_TOLERANCE: add("alert_evidence_capture_is_in_future")
    times = [evidence[k] for k in ("triggered_at", "delivered_at", "acknowledged_at", "resolved_at", "captured_at")]
    if times != sorted(times): add("alert_evidence_timestamps_out_of_order")
    if any(value > reference + FUTURE_TOLERANCE for value in times): add("alert_evidence_timestamp_is_in_future")
    delay = (evidence["delivered_at"] - evidence["triggered_at"]).total_seconds() / 60
    if delay > alert["maximum_detection_delay_minutes"]: add("alert_delivery_delay_exceeds_policy")
    if evidence["severity"] != alert["severity"]: add("alert_evidence_severity_mismatch")
    if evidence["owner"] != alert["owner"]: add("alert_evidence_owner_mismatch")
    if evidence["acknowledging_owner"] != alert["owner"]: add("alert_evidence_acknowledging_owner_mismatch")
    if evidence["runbook"] != alert["runbook"]: add("alert_evidence_runbook_mismatch")
    runbook = repository_root / alert["runbook"].split("#", 1)[0]
    if runbook.is_symlink() or not runbook.is_file(): add("alert_evidence_runbook_unresolved")
    if evidence["delivery_status"] != "delivered": add("alert_delivery_not_confirmed")
    if evidence["notification_count"] != 1: add("alert_notification_count_unexpected")
    if evidence["deployed_asset_fingerprint"] == evidence["destination_fingerprint"]: add("alert_asset_and_destination_fingerprints_overlap")
    if len(findings) > MAX_FINDINGS:
        findings = findings[:MAX_FINDINGS - 1] + [{"category": "alert_findings_truncated"}]
    return {
        "schema_version": 1, "status": "verified" if not findings else "blocked",
        "generated_at_utc": _utc(reference), "target": "dev", "repository": EXPECTED_REPOSITORY,
        "source_commit": evidence["source_commit"], "captured_at_utc": evidence["captured_at_text"],
        "policy_sha256": policy["raw_sha256"], "evidence_manifest_sha256": evidence["raw_sha256"],
        "protected_evidence_sha256": evidence["evidence_sha256"],
        "workspace_fingerprint": evidence["workspace_fingerprint"],
        "deployed_asset_fingerprint": evidence["deployed_asset_fingerprint"],
        "destination_fingerprint": evidence["destination_fingerprint"],
        "alert_event_id": evidence["alert_event_id"], "alert_id": evidence["alert_id"],
        "severity": evidence["severity"], "owner": evidence["owner"], "runbook": evidence["runbook"],
        "triggered_at_utc": evidence["triggered_at_text"], "delivered_at_utc": evidence["delivered_at_text"],
        "acknowledged_at_utc": evidence["acknowledged_at_text"], "resolved_at_utc": evidence["resolved_at_text"],
        "delivery_delay_minutes": delay,
        "maximum_detection_delay_minutes": alert["maximum_detection_delay_minutes"],
        "delivery_attempts": evidence["delivery_attempts"], "notification_count": evidence["notification_count"],
        "delivery_status": evidence["delivery_status"], "acknowledging_owner": evidence["acknowledging_owner"],
        "findings": sorted(findings, key=lambda item: item["category"]),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Alert delivery evidence verification", "", f"- Status: **{report['status']}**",
             f"- Alert: `{report['alert_id']}`", f"- Severity: `{report['severity']}`",
             f"- Owner: `{report['owner']}`", f"- Source commit: `{report['source_commit']}`",
             f"- Delivery delay: `{report['delivery_delay_minutes']}` minutes",
             f"- Maximum delay: `{report['maximum_detection_delay_minutes']}` minutes",
             f"- Notifications: `{report['notification_count']}`", f"- Attempts: `{report['delivery_attempts']}`",
             "", "## Findings", ""]
    lines += [f"- `{item['category']}`" for item in report["findings"]] if report["findings"] else ["- None."]
    lines += ["", "The report contains fingerprints and evidence digests, not destination URLs, credentials, provider responses, raw telemetry rows or notification bodies.", ""]
    return "\n".join(lines)


def _prepare(path: Path) -> Path:
    if path.exists() and path.is_symlink(): raise AlertEvidenceError("alert_output_directory_is_symlink")
    try: path.mkdir(parents=True, exist_ok=True)
    except OSError: raise AlertEvidenceError("alert_output_directory_unavailable") from None
    if path.is_symlink() or not path.is_dir(): raise AlertEvidenceError("alert_output_directory_invalid")
    return path


def _write(path: Path, content: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    if temp.exists() or temp.is_symlink(): raise AlertEvidenceError("alert_temporary_output_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()): raise AlertEvidenceError("alert_output_path_invalid")
    try:
        temp.write_text(content, encoding="utf-8"); temp.replace(path)
    except OSError:
        try: temp.unlink(missing_ok=True)
        except OSError: pass
        raise AlertEvidenceError("alert_output_write_failed") from None


def write_outputs(output_directory: Path, report: Mapping[str, Any]) -> None:
    directory = _prepare(output_directory)
    _write(directory / OUTPUT_JSON, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _write(directory / OUTPUT_MARKDOWN, render_markdown(report))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-age-hours", type=positive_hours, default=DEFAULT_MAX_AGE_HOURS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = verify_evidence(args.policy, args.evidence, max_age_hours=args.max_age_hours)
        write_outputs(args.output_dir, report)
    except AlertEvidenceError as error:
        print(f"Alert delivery evidence verification failed: {error.category}", file=sys.stderr)
        return 2
    print(f"Alert delivery evidence {report['status']}: alert={report['alert_id']}")
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
