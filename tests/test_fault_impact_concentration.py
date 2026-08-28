from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "fault_impact_concentration.sql"


class FaultImpactConcentrationTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_concentration_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "fault_impact_concentration.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Fault impact concentration",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_current_warehouse_views_and_monthly_fault_grain(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        for relation in (
            "fact_machine_failure_event",
            "dim_date",
            "dim_client",
            "dim_site",
            "dim_model",
            "dim_fault",
        ):
            with self.subTest(relation=relation):
                self.assertIn(f"main.lakehouse_demo.{relation}", normalized)

        for join in (
            "failure_fact.date_key = date_dim.date_key",
            "failure_fact.client_key = client_dim.client_key",
            "failure_fact.site_key = site_dim.site_key",
            "failure_fact.model_key = model_dim.model_key",
            "failure_fact.fault_key = fault_dim.fault_key",
        ):
            with self.subTest(join=join):
                self.assertIn(join, normalized)

        self.assertIn(
            "date_trunc('month', cast(date_dim.date_day as timestamp))",
            normalized,
        )
        for grain_column in (
            "client_dim.client_id",
            "site_dim.site_id",
            "model_dim.model",
            "fault_dim.fault_code",
            "fault_dim.severity",
            "fault_dim.severity_rank",
        ):
            self.assertIn(grain_column, normalized)

        for forbidden_relation in (
            "fact_machine_failure_event_history",
            "warehouse_publication_manifest",
        ):
            self.assertNotIn(forbidden_relation, normalized)

    def test_fault_ranking_and_cumulative_windows_are_deterministic(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())
        partition = "partition by event_month, client_id, site_id, model"
        ordering = (
            "order by failure_event_count desc, "
            "attributed_downtime_minutes desc, "
            "maintenance_cost_gbp desc, severity_rank desc, fault_code"
        )

        self.assertGreaterEqual(normalized.count(partition), 7)
        self.assertGreaterEqual(normalized.count(ordering), 4)
        self.assertIn("row_number() over", normalized)
        self.assertIn("fault_impact_rank", normalized)
        self.assertGreaterEqual(
            normalized.count(
                "rows between unbounded preceding and current row"
            ),
            3,
        )

    def test_failure_downtime_and_cost_shares_are_denominator_guarded(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for guard in (
            "when total_failure_event_count > 0",
            "when total_attributed_downtime_minutes > 0",
            "when total_maintenance_cost_gbp > 0",
        ):
            with self.subTest(guard=guard):
                self.assertGreaterEqual(sql.count(guard), 2)

        for metric in (
            "failure_event_share_pct",
            "cumulative_failure_event_share_pct",
            "attributed_downtime_share_pct",
            "cumulative_attributed_downtime_share_pct",
            "maintenance_cost_share_pct",
            "cumulative_maintenance_cost_share_pct",
        ):
            with self.subTest(metric=metric):
                self.assertIn(metric, sql)

    def test_output_preserves_observation_and_attribution_boundaries(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for status in (
            "no_recorded_failures",
            "failures_without_recorded_impact",
            "failures_without_recorded_cost",
            "failure_impact_observed",
        ):
            self.assertIn(status, sql)

        self.assertIn("attributed_downtime", sql)
        self.assertIn("affected_machine_count", sql)
        self.assertIn("severity_rank", sql)
        for overclaim in (
            "root_cause",
            "caused_by",
            "pareto_cause",
            "mttr",
        ):
            with self.subTest(overclaim=overclaim):
                self.assertNotIn(overclaim, sql)

    def test_result_order_follows_month_group_and_fault_rank(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "order by event_month desc, client_id, site_id, model, "
            "fault_impact_rank",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
