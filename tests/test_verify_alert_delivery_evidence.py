from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_alert_delivery_evidence",
    ROOT / "scripts" / "verify_alert_delivery_evidence.py",
)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

POLICY = ROOT / "governance" / "operational_alert_policy.json"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def fingerprint(seed: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


class VerifyAlertDeliveryEvidenceTest(unittest.TestCase):
    def make_manifest(
        self,
        *,
        alert_id: str = "quality_error_check_failed",
        triggered_at: datetime = NOW - timedelta(minutes=20),
        delivered_at: datetime = NOW - timedelta(minutes=10),
        acknowledged_at: datetime = NOW - timedelta(minutes=5),
        resolved_at: datetime = NOW - timedelta(minutes=2),
        captured_at: datetime = NOW - timedelta(minutes=1),
    ) -> dict:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        alert = next(item for item in policy["alerts"] if item["id"] == alert_id)
        return {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
            "workspace_fingerprint": fingerprint("workspace"),
            "alert_event_id": "test-alert-20260825-001",
            "alert_id": alert_id,
            "severity": alert["severity"],
            "owner": alert["owner"],
            "deployed_asset_fingerprint": fingerprint("asset"),
            "destination_fingerprint": fingerprint("destination"),
            "triggered_at_utc": triggered_at.isoformat().replace("+00:00", "Z"),
            "delivered_at_utc": delivered_at.isoformat().replace("+00:00", "Z"),
            "acknowledged_at_utc": acknowledged_at.isoformat().replace("+00:00", "Z"),
            "resolved_at_utc": resolved_at.isoformat().replace("+00:00", "Z"),
            "delivery_attempts": 1,
            "notification_count": 1,
            "delivery_status": "delivered",
            "acknowledging_owner": alert["owner"],
            "runbook": alert["runbook"],
            "test_alert": True,
            "evidence_sha256": fingerprint("protected-evidence"),
        }

    def write_manifest(self, root: Path, manifest: dict) -> Path:
        path = root / "alert-evidence.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_complete_test_alert_is_verified_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_manifest(root, self.make_manifest())
            report = m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)
            m.write_outputs(root / "output", report)
            stored = json.loads((root / "output" / m.OUTPUT_JSON).read_text())
            markdown = (root / "output" / m.OUTPUT_MARKDOWN).read_text()

        self.assertEqual("verified", stored["status"])
        self.assertEqual([], stored["findings"])
        self.assertEqual(10.0, stored["delivery_delay_minutes"])
        self.assertEqual(m.render_markdown(stored), markdown)
        serialized = json.dumps(stored)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("notification_body", serialized)
        self.assertNotIn("provider_response", serialized)

    def test_slow_delivery_and_duplicate_notifications_block(self):
        manifest = self.make_manifest(
            delivered_at=NOW + timedelta(minutes=20),
            acknowledged_at=NOW + timedelta(minutes=21),
            resolved_at=NOW + timedelta(minutes=22),
            captured_at=NOW + timedelta(minutes=23),
        )
        manifest["notification_count"] = 2
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            report = m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)
        categories = {item["category"] for item in report["findings"]}
        self.assertEqual("blocked", report["status"])
        self.assertIn("alert_delivery_delay_exceeds_policy", categories)
        self.assertIn("alert_notification_count_unexpected", categories)
        self.assertIn("alert_evidence_timestamp_is_in_future", categories)

    def test_policy_identity_severity_owner_and_runbook_mismatches_block(self):
        manifest = self.make_manifest()
        manifest["severity"] = "warning"
        manifest["owner"] = "platform"
        manifest["acknowledging_owner"] = "platform"
        manifest["runbook"] = "docs/runbooks/forecast_publication_recovery.md"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            report = m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)
        categories = {item["category"] for item in report["findings"]}
        self.assertIn("alert_evidence_severity_mismatch", categories)
        self.assertIn("alert_evidence_owner_mismatch", categories)
        self.assertIn("alert_evidence_acknowledging_owner_mismatch", categories)
        self.assertIn("alert_evidence_runbook_mismatch", categories)

    def test_delivery_failure_time_order_and_missing_runbook_block(self):
        manifest = self.make_manifest()
        manifest["delivery_status"] = "failed"
        manifest["acknowledged_at_utc"] = (
            NOW - timedelta(minutes=15)
        ).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_manifest(root, manifest)
            empty_repo = root / "repo"
            empty_repo.mkdir()
            report = m.verify_evidence(
                POLICY, path, repository_root=empty_repo, now=NOW
            )
        categories = {item["category"] for item in report["findings"]}
        self.assertIn("alert_delivery_not_confirmed", categories)
        self.assertIn("alert_evidence_timestamps_out_of_order", categories)
        self.assertIn("alert_evidence_runbook_unresolved", categories)

    def test_stale_future_and_overlapping_fingerprints_block(self):
        manifest = self.make_manifest(captured_at=NOW - timedelta(hours=73))
        manifest["destination_fingerprint"] = manifest["deployed_asset_fingerprint"]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            report = m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)
        categories = {item["category"] for item in report["findings"]}
        self.assertIn("alert_evidence_capture_is_stale", categories)
        self.assertIn("alert_asset_and_destination_fingerprints_overlap", categories)

        manifest = self.make_manifest(captured_at=NOW + timedelta(minutes=6))
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            report = m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)
        self.assertIn(
            "alert_evidence_capture_is_in_future",
            {item["category"] for item in report["findings"]},
        )

    def test_unknown_alert_non_dev_and_non_test_manifest_fail_closed(self):
        manifest = self.make_manifest()
        manifest["alert_id"] = "unknown_alert"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            with self.assertRaisesRegex(m.AlertEvidenceError, "alert_id_unknown"):
                m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)

        manifest = self.make_manifest()
        manifest["target"] = "prod"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            with self.assertRaisesRegex(m.AlertEvidenceError, "target_must_be_dev"):
                m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)

        manifest = self.make_manifest()
        manifest["test_alert"] = False
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            with self.assertRaisesRegex(m.AlertEvidenceError, "must_be_test_alert"):
                m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)

    def test_raw_fields_invalid_attempt_bounds_and_bad_policy_fail_closed(self):
        manifest = self.make_manifest()
        manifest["destination_url"] = "forbidden"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            with self.assertRaisesRegex(m.AlertEvidenceError, "shape_invalid"):
                m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)

        manifest = self.make_manifest()
        manifest["delivery_attempts"] = 6
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            with self.assertRaisesRegex(m.AlertEvidenceError, "attempts_invalid"):
                m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)

        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        policy["delivery"]["required_external_evidence"].pop()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy))
            manifest_path = self.write_manifest(root, self.make_manifest())
            with self.assertRaisesRegex(m.AlertEvidenceError, "external_evidence_invalid"):
                m.verify_evidence(
                    policy_path, manifest_path, repository_root=ROOT, now=NOW
                )

    def test_output_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_manifest(root, self.make_manifest())
            report = m.verify_evidence(POLICY, path, repository_root=ROOT, now=NOW)
            target = root / "target"
            target.mkdir()
            link = root / "output"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                m.AlertEvidenceError, "alert_output_directory_is_symlink"
            ):
                m.write_outputs(link, report)


if __name__ == "__main__":
    unittest.main()
