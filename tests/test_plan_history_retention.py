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
    "plan_history_retention", ROOT / "scripts" / "plan_history_retention.py"
)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

POLICY = ROOT / "governance" / "history_retention_policy.json"
OPERATIONAL_POLICY = ROOT / "governance" / "operational_alert_policy.json"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def fingerprint(seed: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


class PlanHistoryRetentionTest(unittest.TestCase):
    def policy_document(self) -> dict:
        return json.loads(POLICY.read_text(encoding="utf-8"))

    def make_inventory(self, *, captured_at: datetime = NOW - timedelta(hours=1)) -> dict:
        policy = self.policy_document()
        captured_text = captured_at.isoformat().replace("+00:00", "Z")
        datasets = []
        for dataset_id in sorted(policy["datasets"]):
            entries = [
                self.entry(
                    dataset_id,
                    "current",
                    captured_at - timedelta(days=1),
                    state="committed",
                    current=True,
                ),
                self.entry(
                    dataset_id,
                    "keep",
                    captured_at
                    - timedelta(
                        days=policy["datasets"][dataset_id]["retention_days"] + 10
                    ),
                    state="committed",
                ),
                self.entry(
                    dataset_id,
                    "candidate",
                    captured_at
                    - timedelta(
                        days=policy["datasets"][dataset_id]["retention_days"] + 20
                    ),
                    state="committed",
                ),
                self.entry(
                    dataset_id,
                    "failed",
                    captured_at
                    - timedelta(
                        days=policy["datasets"][dataset_id]["retention_days"] + 30
                    ),
                    state="failed",
                ),
            ]
            datasets.append({"dataset_id": dataset_id, "entries": entries})
        return {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": captured_text,
            "workspace_fingerprint": fingerprint("workspace"),
            "datasets": datasets,
        }

    def entry(
        self,
        dataset_id: str,
        suffix: str,
        created_at: datetime,
        *,
        state: str,
        current: bool = False,
        recovery_protected: bool = False,
        byte_count: int = 100,
    ) -> dict:
        entry_id = f"{dataset_id}:{suffix}"
        return {
            "entry_id": entry_id,
            "entry_fingerprint": fingerprint(entry_id),
            "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
            "state": state,
            "current": current,
            "recovery_protected": recovery_protected,
            "byte_count": byte_count,
        }

    def write_inventory(self, root: Path, inventory: dict) -> Path:
        path = root / "inventory.json"
        path.write_text(json.dumps(inventory), encoding="utf-8")
        return path

    def test_clean_inventory_produces_dry_run_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.write_inventory(root, self.make_inventory())
            plan = m.build_plan(POLICY, inventory, now=NOW)
            m.write_outputs(root / "output", plan)
            stored = json.loads((root / "output" / m.OUTPUT_JSON).read_text())
            markdown = (root / "output" / m.OUTPUT_MARKDOWN).read_text()

        self.assertEqual("planned", stored["status"])
        self.assertTrue(stored["dry_run_only"])
        self.assertEqual(9, stored["eligible_candidate_count"])
        self.assertEqual(9, len(stored["candidates"]))
        self.assertEqual([], stored["findings"])
        self.assertEqual(m.render_markdown(stored), markdown)
        self.assertNotIn("VACUUM", json.dumps(stored))
        self.assertNotIn("DROP TABLE", json.dumps(stored))

    def test_current_recent_started_recovery_and_minimum_committed_are_protected(self):
        inventory = self.make_inventory()
        dataset = inventory["datasets"][0]
        policy_days = self.policy_document()["datasets"][dataset["dataset_id"]][
            "retention_days"
        ]
        captured = NOW - timedelta(hours=1)
        dataset["entries"].extend(
            [
                self.entry(
                    dataset["dataset_id"],
                    "started",
                    captured - timedelta(days=policy_days + 50),
                    state="started",
                ),
                self.entry(
                    dataset["dataset_id"],
                    "recovery",
                    captured - timedelta(days=policy_days + 50),
                    state="failed",
                    recovery_protected=True,
                ),
                self.entry(
                    dataset["dataset_id"],
                    "recent",
                    captured - timedelta(days=2),
                    state="failed",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            inventory_path = self.write_inventory(Path(directory), inventory)
            plan = m.build_plan(POLICY, inventory_path, now=NOW)
        summary = next(
            item
            for item in plan["datasets"]
            if item["dataset_id"] == dataset["dataset_id"]
        )
        self.assertEqual(1, summary["protected_counts"]["current"])
        self.assertEqual(1, summary["protected_counts"]["started"])
        self.assertEqual(1, summary["protected_counts"]["recovery"])
        self.assertGreaterEqual(summary["protected_counts"]["recent"], 1)
        self.assertEqual(1, summary["protected_counts"]["minimum_committed"])

    def test_multiple_current_entries_and_missing_dataset_fail_closed(self):
        inventory = self.make_inventory()
        dataset = inventory["datasets"][0]
        dataset["entries"][1]["current"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(
                m.RetentionError, "retention_inventory_multiple_current_entries"
            ):
                m.build_plan(POLICY, path, now=NOW)

        inventory = self.make_inventory()
        inventory["datasets"].pop()
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(
                m.RetentionError, "retention_inventory_dataset_coverage_mismatch"
            ):
                m.build_plan(POLICY, path, now=NOW)

    def test_stale_and_future_inventory_block_without_candidates(self):
        for captured_at, expected in (
            (NOW - timedelta(hours=25), "retention_inventory_capture_is_stale"),
            (NOW + timedelta(minutes=6), "retention_inventory_capture_is_in_future"),
        ):
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_inventory(
                        Path(directory), self.make_inventory(captured_at=captured_at)
                    )
                    plan = m.build_plan(POLICY, path, now=NOW)
                self.assertEqual("blocked", plan["status"])
                self.assertEqual([], plan["candidates"])
                self.assertIn(
                    expected, {finding["category"] for finding in plan["findings"]}
                )

    def test_candidate_limit_blocks_and_suppresses_actionable_list(self):
        policy = self.policy_document()
        policy["max_candidates_per_run"] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            inventory_path = self.write_inventory(root, self.make_inventory())
            plan = m.build_plan(policy_path, inventory_path, now=NOW)
        self.assertEqual("blocked", plan["status"])
        self.assertGreater(plan["eligible_candidate_count"], 1)
        self.assertEqual([], plan["candidates"])
        self.assertIn(
            "retention_candidate_count_exceeds_policy",
            {finding["category"] for finding in plan["findings"]},
        )

    def test_unknown_entry_fields_duplicate_ids_and_non_dev_target_are_invalid(self):
        inventory = self.make_inventory()
        inventory["datasets"][0]["entries"][0]["raw_table_name"] = "forbidden"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(m.RetentionError, "entry_shape_invalid"):
                m.build_plan(POLICY, path, now=NOW)

        inventory = self.make_inventory()
        duplicate = dict(inventory["datasets"][0]["entries"][0])
        duplicate["current"] = False
        inventory["datasets"][0]["entries"].append(duplicate)
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(m.RetentionError, "entry_id_duplicate"):
                m.build_plan(POLICY, path, now=NOW)

        inventory = self.make_inventory()
        inventory["target"] = "prod"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_inventory(Path(directory), inventory)
            with self.assertRaisesRegex(m.RetentionError, "target_must_be_dev"):
                m.build_plan(POLICY, path, now=NOW)

    def test_output_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.write_inventory(root, self.make_inventory())
            plan = m.build_plan(POLICY, inventory, now=NOW)
            target = root / "target"
            target.mkdir()
            link = root / "output"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                m.RetentionError, "retention_output_directory_is_symlink"
            ):
                m.write_outputs(link, plan)

    def test_retention_policy_matches_operational_expectations(self):
        retention = self.policy_document()["datasets"]
        operational = json.loads(OPERATIONAL_POLICY.read_text(encoding="utf-8"))[
            "retention_expectations"
        ]
        expected = {
            key.removesuffix("_days"): value for key, value in operational.items()
        }
        self.assertEqual(
            expected,
            {key: value["retention_days"] for key, value in retention.items()},
        )
        self.assertTrue(self.policy_document()["dry_run_only"])


if __name__ == "__main__":
    unittest.main()
