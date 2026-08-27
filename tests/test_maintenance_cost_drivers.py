from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "maintenance_cost_drivers.sql"


class MaintenanceCostDriversTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_cost_driver_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "maintenance_cost_drivers.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Maintenance cost drivers",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_committed_gold_views_and_monthly_business_grain(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "from main.lakehouse_demo.gold_maintenance_costs",
            normalized,
        )
        self.assertIn(
            "from main.lakehouse_demo.gold_parts_usage",
            normalized,
        )
        self.assertIn(
            "group by event_month, client_id, site_id, model",
            normalized,
        )
        self.assertIn(
            "date_trunc('month', cast(event_date as timestamp))",
            normalized,
        )
        self.assertIn(
            "partition by event_month, client_id, site_id, model",
            normalized,
        )
        self.assertNotIn("_history", normalized)
        self.assertNotIn("publication_manifest", normalized)

    def test_top_part_selection_is_deterministic_and_model_safe(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("row_number() over", normalized)
        self.assertIn(
            "order by part_quantity desc, associated_cost_gbp desc, part_code",
            normalized,
        )
        self.assertIn("m.event_month = p.event_month", normalized)
        self.assertIn("m.client_id = p.client_id", normalized)
        self.assertIn("m.site_id = p.site_id", normalized)
        self.assertIn("m.model <=> p.model", normalized)
        self.assertIn("p.part_rank = 1", normalized)
        self.assertNotIn("sum(machine_count)", normalized)

    def test_cost_and_downtime_ratios_are_denominator_guarded(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for required in (
            "when m.maintenance_event_count > 0",
            "cost_per_maintenance_event_gbp",
            "when m.failure_event_count > 0",
            "cost_per_failure_event_gbp",
            "attributed_downtime_per_failure_minutes",
            "when m.maintenance_cost_gbp > 0",
            "top_recorded_part_cost_share_pct",
        ):
            with self.subTest(required=required):
                self.assertIn(required, sql)

    def test_observation_statuses_preserve_missing_evidence_boundaries(self):
        sql = QUERY.read_text(encoding="utf-8")

        for status in (
            "no_recorded_maintenance_impact",
            "maintenance_without_recorded_failure",
            "failure_without_recorded_part",
            "failure_with_recorded_part",
        ):
            self.assertIn(status, sql)
        for misleading_label in (
            "root_cause",
            "caused_by_part",
            "mttr",
        ):
            self.assertNotIn(misleading_label, sql.lower())
        self.assertIn("m.maintenance_cost_gbp DESC", sql)
        self.assertIn("m.attributed_downtime_minutes DESC", sql)


if __name__ == "__main__":
    unittest.main()
