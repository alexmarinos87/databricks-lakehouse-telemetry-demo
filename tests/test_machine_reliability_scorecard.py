from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "machine_reliability_scorecard.sql"


class MachineReliabilityScorecardTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_reliability_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "machine_reliability_scorecard.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Machine reliability scorecard",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_committed_gold_views_and_model_safe_attribution(self):
        sql = QUERY.read_text(encoding="utf-8")
        normalized = " ".join(sql.lower().split())

        self.assertIn(
            "from main.lakehouse_demo.gold_machine_uptime",
            normalized,
        )
        self.assertIn(
            "from main.lakehouse_demo.gold_failure_events",
            normalized,
        )
        self.assertGreaterEqual(
            normalized.count("group by client_id, site_id, machine_id, model"),
            2,
        )
        self.assertIn("u.model <=> f.model", normalized)
        self.assertNotIn("_history", normalized)
        self.assertNotIn("publication_manifest", normalized)

    def test_rate_and_per_failure_metrics_are_denominator_guarded(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for required in (
            "when u.running_minutes > 0",
            "coalesce(f.failure_event_count, 0)",
            "u.running_minutes / 60.0",
            "failures_per_100_operating_hours",
            "when coalesce(f.failure_event_count, 0) > 0",
            "avg_attributed_downtime_per_failure_minutes",
            "avg_failure_related_cost_gbp",
            "downtime_semantics_version",
        ):
            with self.subTest(required=required):
                self.assertIn(required, sql)

        for misleading_label in ("mtbf", "mttr", "mean_time_between", "mean_time_to_repair"):
            with self.subTest(label=misleading_label):
                self.assertNotIn(misleading_label, sql)

    def test_observation_status_distinguishes_zero_time_and_zero_failures(self):
        sql = QUERY.read_text(encoding="utf-8")

        for status in (
            "no_operating_time",
            "no_recorded_failures",
            "observed_failures",
        ):
            self.assertIn(status, sql)
        self.assertIn("DESC NULLS LAST", sql)


if __name__ == "__main__":
    unittest.main()
