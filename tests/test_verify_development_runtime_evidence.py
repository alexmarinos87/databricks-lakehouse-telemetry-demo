from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_development_runtime_evidence",
    ROOT / "scripts" / "verify_development_runtime_evidence.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def fingerprint(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class VerifyDevelopmentRuntimeEvidenceTest(unittest.TestCase):
    def make_manifest(
        self,
        *,
        captured_at: datetime = NOW - timedelta(minutes=5),
        started_at: datetime = NOW - timedelta(hours=1),
        completed_at: datetime = NOW - timedelta(minutes=20),
    ) -> dict:
        execution = fingerprint("execution")
        observed_at = utc(completed_at)
        return {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": utc(captured_at),
            "apply": {
                "authorized": True,
                "approved_at_utc": utc(started_at - timedelta(minutes=10)),
                "approval_sha256": fingerprint("approval"),
                "accepted_plan_sha256": fingerprint("plan"),
                "accepted_plan_review_sha256": fingerprint("plan-review"),
                "workflow_run_fingerprint": fingerprint("workflow-run"),
            },
            "execution": {
                "execution_fingerprint": execution,
                "evidence_sha256": fingerprint("execution-evidence"),
                "started_at_utc": utc(started_at),
                "completed_at_utc": utc(completed_at),
                "production_contact": False,
                "deployment_principal_fingerprint": fingerprint("deployment"),
                "runtime_principal_fingerprint": fingerprint("runtime"),
            },
            "evidence_families": [
                {
                    "family": family,
                    "execution_fingerprint": execution,
                    "observed_at_utc": observed_at,
                    "evidence_sha256": fingerprint(f"family-{family}"),
                    "record_count": 1,
                }
                for family in m.REQUIRED_FAMILIES
            ],
            "assertions": [
                {
                    "assertion_id": assertion_id,
                    "family": family,
                    "execution_fingerprint": execution,
                    "status": "passed",
                    "observed_at_utc": observed_at,
                    "evidence_sha256": fingerprint(f"assertion-{assertion_id}"),
                }
                for assertion_id, family in m.ASSERTION_FAMILIES.items()
            ],
            "rollback": {
                "tested": True,
                "completed_at_utc": utc(captured_at - timedelta(minutes=1)),
                "evidence_sha256": fingerprint("rollback"),
                "recovery_point_sha256": fingerprint("recovery-point"),
            },
        }

    @staticmethod
    def write_manifest(root: Path, manifest: dict) -> Path:
        path = root / "runtime-evidence.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def evaluate(self, manifest: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            return m.verify_evidence(path, now=NOW)

    def test_complete_manifest_is_verified_evidence_bound_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_manifest(root, self.make_manifest())
            report = m.verify_evidence(path, now=NOW)
            m.write_outputs(root / "output", report)
            stored = json.loads((root / "output" / m.OUTPUT_JSON).read_text())
            markdown = (root / "output" / m.OUTPUT_MARKDOWN).read_text()

        self.assertEqual("verified", stored["status"])
        self.assertEqual(len(m.REQUIRED_FAMILIES), stored["evidence_family_count"])
        self.assertEqual(len(m.ASSERTION_FAMILIES), stored["assertion_count"])
        self.assertEqual(fingerprint("execution-evidence"), stored["execution"]["evidence_sha256"])
        self.assertEqual([], stored["findings"])
        self.assertEqual(m.render_markdown(stored), markdown)
        rendered = json.dumps(stored)
        for forbidden in (
            "lakehouse-demo-ci",
            "lakehouse-demo-runtime",
            "table_name",
            "provider_response",
            "https://",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_missing_family_and_assertion_block(self):
        manifest = self.make_manifest()
        missing_family = manifest["evidence_families"].pop()["family"]
        missing_assertion = manifest["assertions"].pop()["assertion_id"]
        report = self.evaluate(manifest)
        findings = {(item["category"], item.get("scope")) for item in report["findings"]}
        self.assertIn(("required_evidence_family_missing", missing_family), findings)
        self.assertIn(("required_assertion_missing", missing_assertion), findings)
        self.assertEqual("blocked", report["status"])

    def test_authorization_production_and_identity_overlap_block(self):
        manifest = self.make_manifest()
        manifest["apply"]["authorized"] = False
        manifest["execution"]["production_contact"] = True
        manifest["execution"]["runtime_principal_fingerprint"] = manifest["execution"][
            "deployment_principal_fingerprint"
        ]
        categories = {item["category"] for item in self.evaluate(manifest)["findings"]}
        self.assertIn("development_apply_was_not_authorized", categories)
        self.assertIn("production_contact_was_reported", categories)
        self.assertIn("deployment_and_runtime_identities_overlap", categories)

    def test_failed_and_not_tested_assertions_block(self):
        manifest = self.make_manifest()
        manifest["assertions"][0]["status"] = "failed"
        manifest["assertions"][1]["status"] = "not_tested"
        categories = {item["category"] for item in self.evaluate(manifest)["findings"]}
        self.assertIn("runtime_assertion_failed", categories)
        self.assertIn("runtime_assertion_not_tested", categories)

    def test_execution_family_and_assertion_mismatch_block(self):
        manifest = self.make_manifest(
            started_at=NOW - timedelta(hours=5),
            completed_at=NOW - timedelta(minutes=20),
        )
        manifest["evidence_families"][0]["execution_fingerprint"] = fingerprint("other")
        manifest["assertions"][0]["family"] = "gold"
        manifest["assertions"][1]["execution_fingerprint"] = fingerprint("other")
        categories = {item["category"] for item in self.evaluate(manifest)["findings"]}
        self.assertIn("execution_duration_exceeds_limit", categories)
        self.assertIn("evidence_family_execution_mismatch", categories)
        self.assertIn("assertion_family_mismatch", categories)
        self.assertIn("assertion_execution_mismatch", categories)

    def test_stale_future_and_after_capture_evidence_block(self):
        stale = self.make_manifest(
            captured_at=NOW - timedelta(hours=73),
            started_at=NOW - timedelta(hours=74),
            completed_at=NOW - timedelta(hours=73, minutes=30),
        )
        stale_categories = {item["category"] for item in self.evaluate(stale)["findings"]}
        self.assertIn("evidence_capture_is_stale", stale_categories)
        self.assertIn("execution_is_stale", stale_categories)

        future = self.make_manifest(captured_at=NOW + timedelta(minutes=6))
        future["evidence_families"][0]["observed_at_utc"] = utc(NOW + timedelta(minutes=7))
        future["rollback"]["completed_at_utc"] = utc(NOW + timedelta(minutes=8))
        future_categories = {item["category"] for item in self.evaluate(future)["findings"]}
        self.assertIn("evidence_capture_is_in_future", future_categories)
        self.assertIn("evidence_family_timestamp_outside_window", future_categories)
        self.assertIn("rollback_is_in_future", future_categories)

    def test_rollback_not_tested_or_timed_before_execution_blocks(self):
        manifest = self.make_manifest()
        manifest["rollback"]["tested"] = False
        manifest["rollback"]["completed_at_utc"] = manifest["execution"]["started_at_utc"]
        categories = {item["category"] for item in self.evaluate(manifest)["findings"]}
        self.assertIn("rollback_was_not_tested", categories)
        self.assertIn("rollback_completed_before_execution", categories)

    def test_execution_digest_duplicate_unknown_and_raw_fields_fail_closed(self):
        missing_digest = self.make_manifest()
        missing_digest["execution"].pop("evidence_sha256")
        duplicate = self.make_manifest()
        duplicate["assertions"].append(dict(duplicate["assertions"][0]))
        unknown = self.make_manifest()
        unknown["evidence_families"][0]["family"] = "raw_tables"
        raw = self.make_manifest()
        raw["provider_response"] = {"table_name": "forbidden"}

        cases = (
            (missing_digest, "execution_shape_invalid"),
            (duplicate, "assertion_id_duplicate"),
            (unknown, "evidence_family_name_unsupported"),
            (raw, "evidence_shape_invalid"),
        )
        for manifest, category in cases:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                path = self.write_manifest(Path(directory), manifest)
                with self.assertRaisesRegex(m.VerificationError, category):
                    m.verify_evidence(path, now=NOW)

    def test_non_dev_target_and_symbolic_links_are_rejected(self):
        manifest = self.make_manifest()
        manifest["target"] = "prod"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_manifest(Path(directory), manifest)
            with self.assertRaisesRegex(m.VerificationError, "evidence_target_must_be_dev"):
                m.verify_evidence(path, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = self.write_manifest(root, self.make_manifest())
            link = root / "linked.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(m.VerificationError, "evidence_file_invalid"):
                m.verify_evidence(link, now=NOW)
            report = m.verify_evidence(real, now=NOW)
            target = root / "target"
            target.mkdir()
            output = root / "output"
            output.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(m.VerificationError, "output_directory_is_symlink"):
                m.write_outputs(output, report)

    def test_source_and_documentation_preserve_offline_human_review_boundary(self):
        source = (ROOT / "scripts" / "verify_development_runtime_evidence.py").read_text()
        brief = (
            ROOT / "docs" / "change_briefs" / "verify_development_runtime_evidence.md"
        ).read_text()
        guide = (ROOT / "docs" / "development_runtime_evidence.md").read_text()
        for token in (
            "REQUIRED_FAMILIES",
            "ASSERTION_FAMILIES",
            "production_contact_was_reported",
            "rollback_was_not_tested",
            "execution_evidence_digest_invalid",
            "development-runtime-verification.json",
            "development-runtime-verification.md",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for forbidden in ("subprocess", "urllib", "requests.", "DATABRICKS_TOKEN"):
            self.assertNotIn(forbidden, source)
        self.assertIn("controlled development run", brief)
        self.assertIn("does not authorize another deployment", brief)
        self.assertIn("execution evidence digest", brief)
        self.assertIn("accepted_plan_review_sha256", guide)
        self.assertIn("evidence_sha256", guide)
        self.assertIn("production_contact: false", guide)
        self.assertIn("Human review remains required", guide)


if __name__ == "__main__":
    unittest.main()
