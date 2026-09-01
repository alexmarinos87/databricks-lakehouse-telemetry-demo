from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = (
    ROOT
    / "sql"
    / "reporting_assets"
    / "monthly_part_usage_concentration.sql"
)


def normalized_sql() -> str:
    return " ".join(QUERY.read_text(encoding="utf-8").lower().split())


class MonthlyPartUsageConcentrationTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_concentration_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "monthly_part_usage_concentration.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Monthly part usage concentration",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_current_failure_fact_and_dimensions(self):
        normalized = normalized_sql()

        for relation in (
            "fact_machine_failure_event",
            "dim_date",
            "dim_client",
            "dim_site",
            "dim_model",
        ):
            with self.subTest(relation=relation):
                self.assertIn(f"main.lakehouse_demo.{relation}", normalized)

        for forbidden in (
            "fact_machine_failure_event_history",
            "warehouse_publication_manifest",
            "gold_parts_usage",
            "gold_failure_events_history",
            "sum(machine_count)",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, normalized)

    def test_monthly_part_grain_and_exact_observation_counts_are_explicit(self):
        normalized = normalized_sql()

        self.assertIn(
            "date_trunc('month', cast(date_dim.date_day as timestamp)) "
            "as event_month",
            normalized,
        )
        self.assertIn(
            "group by date_trunc('month', cast(date_dim.date_day as timestamp)), "
            "client_dim.client_id, site_dim.site_id, model_dim.model, "
            "failure_fact.part_code",
            normalized,
        )
        for expression in (
            "sum(failure_fact.failure_event_count) as observed_part_event_count",
            "count(distinct failure_fact.event_id) as observed_part_identity_count",
            "count(distinct failure_fact.machine_key) as affected_machine_count",
            "count(distinct failure_fact.date_key) as observed_part_day_count",
            "count(distinct failure_fact.fault_key) as associated_fault_count",
            "sum(coalesce(failure_fact.part_quantity, 0)) as recorded_part_quantity",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, normalized)

        self.assertIn("failure_fact.part_code is not null", normalized)
        self.assertIn("upper(trim(failure_fact.part_code)) <> 'none'", normalized)
        self.assertIn("coalesce(failure_fact.part_quantity, 0) > 0", normalized)

    def test_rank_and_cumulative_windows_use_one_deterministic_order(self):
        normalized = normalized_sql()
        ordering = (
            "partition by event_month, client_id, site_id, model order by "
            "recorded_part_quantity desc, observed_part_event_count desc, "
            "associated_failure_cost_gbp desc, "
            "associated_attributed_downtime_minutes desc, part_code"
        )

        self.assertGreaterEqual(normalized.count(ordering), 5)
        self.assertIn("row_number() over", normalized)
        self.assertIn("as part_usage_rank", normalized)
        self.assertEqual(
            4,
            normalized.count("rows between unbounded preceding and current row"),
        )
        for cumulative in (
            "cumulative_recorded_part_quantity",
            "cumulative_observed_part_event_count",
            "cumulative_associated_attributed_downtime_minutes",
            "cumulative_associated_failure_cost_gbp",
        ):
            self.assertIn(cumulative, normalized)

    def test_quantity_event_downtime_and_cost_shares_are_guarded(self):
        normalized = normalized_sql()

        for denominator in (
            "total_recorded_part_quantity",
            "total_observed_part_event_count",
            "total_associated_attributed_downtime_minutes",
            "total_associated_failure_cost_gbp",
        ):
            with self.subTest(denominator=denominator):
                self.assertGreaterEqual(
                    normalized.count(f"when {denominator} > 0 then"),
                    2,
                )

        for share in (
            "recorded_part_quantity_share_pct",
            "cumulative_recorded_part_quantity_share_pct",
            "observed_part_event_share_pct",
            "cumulative_observed_part_event_share_pct",
            "associated_attributed_downtime_share_pct",
            "cumulative_associated_attributed_downtime_share_pct",
            "associated_failure_cost_share_pct",
            "cumulative_associated_failure_cost_share_pct",
        ):
            self.assertIn(share, normalized)

    def test_status_and_scope_do_not_overclaim_demand_or_causation(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for status in (
            "part_quantity_without_recorded_impact",
            "part_quantity_without_recorded_cost",
            "part_usage_evidence_observed",
            "observed_failure_part_records_only",
        ):
            self.assertIn(status, sql)

        for overclaim in (
            "demand_forecast",
            "stock_recommendation",
            "reorder_point",
            "root_cause",
            "caused_by",
            "part_price",
            "mtbf",
            "mttr",
        ):
            with self.subTest(overclaim=overclaim):
                self.assertNotIn(overclaim, sql)

    def test_result_order_follows_month_group_and_part_rank(self):
        normalized = normalized_sql()

        self.assertIn(
            "order by event_month desc, client_id, site_id, model, "
            "part_usage_rank, part_code",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
