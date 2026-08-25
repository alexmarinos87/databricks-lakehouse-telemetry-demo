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
    "plan_retention_dry_run",
    ROOT / "scripts" / "plan_retention_dry_run.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

POLICY = ROOT / "governance" / "operational_alert_policy.json"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def fingerprint(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PlanRetentionDryRunTest(unittest.TestCase):
    def make_inventory(self, *, captured_at: datetime = NOW - timedelta(hours=1)) -> dict:
        days = {
            "quality_check_results_days": 90,
            "quality_metric_history_days": 180,
            "forecast_history_days": 180,
            "forecast_publication_manifest_days": 365,
            "expectation_event_log_days": 90,
        }
        relations = []
        for index, (key, retention_days) in enumerate(sorted(days.items()), start=1):
            relations.append(
                {
                    "retention_key": key,
                    "relation_fingerprint": fingerprint(f"relation-{key}"),
                    "current_version": 100 + index,
                    "recovery_version": 100,
                    "latest_committed_at_utc": utc(captured_at - timedelta(minutes=5)),
                    "candidate_latest_at_utc": utc(
                        captured_at - timedelta(days=retention_days, minutes=1)
                    ),
                    "candidate_rows": index * 10,
                    "candidate_bytes": index * 1000,
                    "candidate_versions": index,
                    "evidence_sha256": fingerprint(f"evidence-{key}"),
                }
            )
        return {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": utc(captured_at),
            "workspace_fingerprint": fingerprint("workspace"),
            "legal_hold": False,
            "legal_hold_evidence_sha256": fingerprint("legal-hold-state"),
            "active_incident": False,
            "active_incident_evidence_sha256": fingerprint("incident-state"),
            "recovery": {
                "verified": True,
                "evidence_sha256": fingerprint("recovery"),
                "recovery_window_hours": 168,
            },
            "relations": relations,
        }

    @staticmethod
    def write_inventory(root: Path, inventory: dict) -> Path:
        path = root / "retention-inventory.json"
        path.write_text(json.dumps(inventory), encoding="utf-8")
        return path

    def evaluate(self, inventory: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            return m.create_plan(POLICY, path, now=NOW)

    def test_complete_inventory_produces_ready_evidence_bound_sanitized_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.write_inventory(root, self.make_inventory())
            plan = m.create_plan(POLICY, inventory, now=NOW)
            m.write_outputs(root / "output", plan)
            stored = json.loads((root / "output" / m.OUTPUT_JSON).read_text())
            markdown = (root / "output" / m.OUTPUT_MARKDOWN).read_text()

        self.assertEqual("ready", stored["status"])
        self.assertTrue(stored["dry_run_only"])
        self.assertFalse(stored["execution_authorized"])
        self.assertEqual(5, stored["relation_count"])
        self.assertEqual(fingerprint("legal-hold-state"), stored["legal_hold_evidence_sha256"])
        self.assertEqual(fingerprint("incident-state"), stored["active_incident_evidence_sha256"])
        self.assertEqual([], stored["findings"])
        self.assertEqual(m.render_markdown(stored), markdown)
        rendered = json.dumps(stored)
        for forbidden in ("main.lakehouse", "provider_response", "https://"):
            self.assertNotIn(forbidden, rendered)

    def test_missing_relation_blocks(self):
        inventory = self.make_inventory()
        missing = inventory["relations"].pop()["retention_key"]
        plan = self.evaluate(inventory)
        self.assertEqual("blocked", plan["status"])
        self.assertIn(
            ("required_retention_relation_missing", missing),
            {(item["category"], item.get("scope")) for item in plan["findings"]},
        )

    def test_hold_incident_and_recovery_gaps_block(self):
        inventory = self.make_inventory()
        inventory["legal_hold"] = True
        inventory["active_incident"] = True
        inventory["recovery"]["verified"] = False
        inventory["recovery"]["recovery_window_hours"] = 24
        categories = {item["category"] for item in self.evaluate(inventory)["findings"]}
        self.assertIn("legal_hold_is_active", categories)
        self.assertIn("active_incident_blocks_retention", categories)
        self.assertIn("recovery_evidence_is_not_verified", categories)
        self.assertIn("recovery_window_is_too_short", categories)

    def test_candidate_boundary_and_version_inconsistency_block(self):
        inventory = self.make_inventory()
        relation = inventory["relations"][0]
        relation["candidate_latest_at_utc"] = inventory["captured_at_utc"]
        relation["recovery_version"] = relation["current_version"] + 1
        relation["candidate_versions"] = relation["current_version"] + 1
        categories = {item["category"] for item in self.evaluate(inventory)["findings"]}
        self.assertIn("candidate_boundary_is_not_older_than_cutoff", categories)
        self.assertIn("candidate_is_after_latest_commit", categories)
        self.assertIn("recovery_version_exceeds_current", categories)
        self.assertIn("candidate_version_count_exceeds_current", categories)

    def test_candidate_count_shapes_must_be_consistent(self):
        for field in ("candidate_rows", "candidate_bytes", "candidate_versions"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                inventory = self.make_inventory()
                inventory["relations"][0][field] = 0
                path = self.write_inventory(Path(directory), inventory)
                with self.assertRaisesRegex(m.PlanError, "candidate_counts_are_inconsistent"):
                    m.create_plan(POLICY, path, now=NOW)

        zero = self.make_inventory()
        for field in ("candidate_rows", "candidate_bytes", "candidate_versions"):
            zero["relations"][0][field] = 0
        self.assertEqual("ready", self.evaluate(zero)["status"])

    def test_stale_and_future_inventory_block(self):
        for captured_at, expected in (
            (NOW - timedelta(hours=73), "inventory_is_stale"),
            (NOW + timedelta(minutes=6), "inventory_is_in_future"),
        ):
            with self.subTest(expected=expected):
                categories = {
                    item["category"]
                    for item in self.evaluate(self.make_inventory(captured_at=captured_at))[
                        "findings"
                    ]
                }
                self.assertIn(expected, categories)

    def test_control_digests_duplicate_unknown_and_raw_fields_fail_closed(self):
        missing_hold_digest = self.make_inventory()
        missing_hold_digest.pop("legal_hold_evidence_sha256")
        duplicate = self.make_inventory()
        duplicate["relations"].append(dict(duplicate["relations"][0]))
        unknown = self.make_inventory()
        unknown["relations"][0]["retention_key"] = "raw_telemetry_days"
        raw = self.make_inventory()
        raw["table_name"] = "main.lakehouse_demo_dev.secret"
        cases = (
            (missing_hold_digest, "inventory_shape_invalid"),
            (duplicate, "retention_key_duplicate"),
            (unknown, "retention_key_unsupported"),
            (raw, "inventory_shape_invalid"),
        )
        for inventory, category in cases:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                path = self.write_inventory(Path(directory), inventory)
                with self.assertRaisesRegex(m.PlanError, category):
                    m.create_plan(POLICY, path, now=NOW)

    def test_relation_fingerprint_and_numeric_bounds_fail_closed(self):
        duplicate = self.make_inventory()
        duplicate["relations"][1]["relation_fingerprint"] = duplicate["relations"][0][
            "relation_fingerprint"
        ]
        too_large = self.make_inventory()
        too_large["relations"][0]["candidate_bytes"] = m.MAX_BYTES + 1
        for inventory, category in (
            (duplicate, "relation_fingerprint_duplicate"),
            (too_large, "candidate_bytes_invalid"),
        ):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                path = self.write_inventory(Path(directory), inventory)
                with self.assertRaisesRegex(m.PlanError, category):
                    m.create_plan(POLICY, path, now=NOW)

    def test_non_dev_target_and_symbolic_links_are_rejected(self):
        inventory = self.make_inventory()
        inventory["target"] = "prod"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(m.PlanError, "inventory_target_must_be_dev"):
                m.create_plan(POLICY, path, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = self.write_inventory(root, self.make_inventory())
            link = root / "linked.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(m.PlanError, "inventory_file_invalid"):
                m.create_plan(POLICY, link, now=NOW)
            plan = m.create_plan(POLICY, real, now=NOW)
            target = root / "target"
            target.mkdir()
            output = root / "output"
            output.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(m.PlanError, "output_directory_is_symlink"):
                m.write_outputs(output, plan)

    def test_source_and_docs_keep_plan_evidence_bound_and_non_mutating(self):
        source = (ROOT / "scripts" / "plan_retention_dry_run.py").read_text()
        brief = (ROOT / "docs" / "change_briefs" / "plan_retention_dry_run.md").read_text()
        guide = (ROOT / "docs" / "retention_dry_run.md").read_text()
        for forbidden in ("subprocess", "urllib", "requests.", "DATABRICKS_TOKEN"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"dry_run_only": True', source)
        self.assertIn('"execution_authorized": False', source)
        self.assertIn("legal_hold_evidence_digest_invalid", source)
        self.assertIn("active_incident_evidence_digest_invalid", source)
        self.assertIn("candidate_counts_are_inconsistent", source)
        self.assertIn("never authorizes mutation", brief)
        self.assertIn("protected evidence digests", brief)
        self.assertIn("legal_hold_evidence_sha256", guide)
        self.assertIn("active_incident_evidence_sha256", guide)
        self.assertIn("does not execute `DELETE`, `VACUUM`, or `DROP`", guide)
        self.assertIn("Human approval is still required", guide)


if __name__ == "__main__":
    unittest.main()
