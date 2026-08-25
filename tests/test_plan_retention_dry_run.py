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
        relations = []
        days = {
            "quality_check_results_days": 90,
            "quality_metric_history_days": 180,
            "forecast_history_days": 180,
            "forecast_publication_manifest_days": 365,
            "expectation_event_log_days": 90,
        }
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
            "active_incident": False,
            "recovery": {
                "verified": True,
                "evidence_sha256": fingerprint("recovery"),
                "recovery_window_hours": 168,
            },
            "relations": relations,
        }

    def write_inventory(self, root: Path, inventory: dict) -> Path:
        path = root / "retention-inventory.json"
        path.write_text(json.dumps(inventory), encoding="utf-8")
        return path

    def test_complete_inventory_produces_ready_sanitized_plan(self):
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
        self.assertEqual([], stored["findings"])
        self.assertEqual(m.render_markdown(stored), markdown)
        rendered = json.dumps(stored)
        self.assertNotIn("main.lakehouse", rendered)
        self.assertNotIn("provider_response", rendered)
        self.assertNotIn("https://", rendered)

    def test_missing_relation_blocks(self):
        inventory = self.make_inventory()
        missing = inventory["relations"].pop()["retention_key"]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            plan = m.create_plan(POLICY, path, now=NOW)
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
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            plan = m.create_plan(POLICY, path, now=NOW)
        categories = {item["category"] for item in plan["findings"]}
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
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            plan = m.create_plan(POLICY, path, now=NOW)
        categories = {item["category"] for item in plan["findings"]}
        self.assertIn("candidate_boundary_is_not_older_than_cutoff", categories)
        self.assertIn("candidate_is_after_latest_commit", categories)
        self.assertIn("recovery_version_exceeds_current", categories)
        self.assertIn("candidate_version_count_exceeds_current", categories)

    def test_stale_and_future_inventory_block(self):
        for captured_at, expected in (
            (NOW - timedelta(hours=73), "inventory_is_stale"),
            (NOW + timedelta(minutes=6), "inventory_is_in_future"),
        ):
            with self.subTest(expected=expected):
                inventory = self.make_inventory(captured_at=captured_at)
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_inventory(Path(directory), inventory)
                    plan = m.create_plan(POLICY, path, now=NOW)
                self.assertIn(expected, {item["category"] for item in plan["findings"]})

    def test_duplicate_unknown_and_raw_fields_fail_closed(self):
        duplicate = self.make_inventory()
        duplicate["relations"].append(dict(duplicate["relations"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), duplicate)
            with self.assertRaisesRegex(m.PlanError, "retention_key_duplicate"):
                m.create_plan(POLICY, path, now=NOW)

        unknown = self.make_inventory()
        unknown["relations"][0]["retention_key"] = "raw_telemetry_days"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), unknown)
            with self.assertRaisesRegex(m.PlanError, "retention_key_unsupported"):
                m.create_plan(POLICY, path, now=NOW)

        raw = self.make_inventory()
        raw["table_name"] = "main.lakehouse_demo_dev.secret"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), raw)
            with self.assertRaisesRegex(m.PlanError, "inventory_shape_invalid"):
                m.create_plan(POLICY, path, now=NOW)

    def test_relation_fingerprint_and_numeric_bounds_fail_closed(self):
        inventory = self.make_inventory()
        inventory["relations"][1]["relation_fingerprint"] = inventory["relations"][0][
            "relation_fingerprint"
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(m.PlanError, "relation_fingerprint_duplicate"):
                m.create_plan(POLICY, path, now=NOW)

        inventory = self.make_inventory()
        inventory["relations"][0]["candidate_bytes"] = m.MAX_BYTES + 1
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(m.PlanError, "candidate_bytes_invalid"):
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

    def test_source_and_docs_keep_plan_non_mutating(self):
        source = (ROOT / "scripts" / "plan_retention_dry_run.py").read_text()
        brief = (
            ROOT / "docs" / "change_briefs" / "plan_retention_dry_run.md"
        ).read_text()
        guide = (ROOT / "docs" / "retention_dry_run.md").read_text()

        self.assertNotIn("subprocess", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("DATABRICKS_TOKEN", source)
        self.assertIn('"dry_run_only": True', source)
        self.assertIn('"execution_authorized": False', source)
        self.assertIn("never authorizes mutation", brief)
        self.assertIn("does not execute `DELETE`, `VACUUM`, or `DROP`", guide)
        self.assertIn("Human approval is still required", guide)


if __name__ == "__main__":
    unittest.main()
