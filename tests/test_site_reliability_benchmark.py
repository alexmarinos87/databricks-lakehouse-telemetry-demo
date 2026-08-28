from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "site_reliability_benchmark.sql"


class SiteReliabilityBenchmarkTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_benchmark_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "site_reliability_benchmark.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Site reliability benchmark",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_current_gold_views_at_monthly_site_model_grain(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "from main.lakehouse_demo.gold_machine_uptime",
            normalized,
        )
        self.assertIn(
            "from main.lakehouse_demo.gold_failure_events",
            normalized,
        )
        monthly_group = (
            "group by date_trunc('month', cast(event_date as timestamp)), "
            "client_id, site_id, model"
        )
        self.assertGreaterEqual(normalized.count(monthly_group), 2)
        for forbidden_relation in (
            "gold_machine_uptime_history",
            "gold_failure_events_history",
            "gold_publication_manifest",
        ):
            self.assertNotIn(forbidden_relation, normalized)

    def test_complete_month_keys_and_null_safe_model_attribution_are_explicit(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("monthly_keys as", normalized)
        self.assertIn(
            "select event_month, client_id, site_id, model from monthly_uptime "
            "union select event_month, client_id, site_id, model "
            "from monthly_failures",
            normalized,
        )
        self.assertIn("keys.model <=> uptime.model", normalized)
        self.assertIn("keys.model <=> failures.model", normalized)
        for join in (
            "keys.event_month = uptime.event_month",
            "keys.client_id = uptime.client_id",
            "keys.site_id = uptime.site_id",
            "keys.event_month = failures.event_month",
            "keys.client_id = failures.client_id",
            "keys.site_id = failures.site_id",
        ):
            with self.subTest(join=join):
                self.assertIn(join, normalized)

    def test_benchmark_rates_are_weighted_from_raw_totals_not_site_averages(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())
        partition = "partition by event_month, client_id, model"

        for expression in (
            "sum(running_minutes) over",
            "sum(observed_minutes) over",
            "sum(failure_event_count) over",
            "sum(failure_related_cost_gbp) over",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, normalized)
        self.assertGreaterEqual(normalized.count(partition), 7)
        self.assertIn(
            "client_model_running_minutes / client_model_observed_minutes * 100",
            normalized,
        )
        self.assertNotIn("avg(weighted_uptime_pct)", normalized)
        self.assertNotIn(
            "avg(failure_events_per_100_operating_hours)",
            normalized,
        )

    def test_site_and_benchmark_denominators_are_guarded(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        for guard in (
            "when coalesce(uptime.observed_minutes, 0) > 0",
            "when coalesce(uptime.running_minutes, 0) > 0",
            "when client_model_observed_minutes > 0",
            "when client_model_running_minutes > 0",
        ):
            with self.subTest(guard=guard):
                self.assertIn(guard, normalized)

        for metric in (
            "weighted_uptime_pct_vs_client_model",
            "failure_rate_vs_client_model",
            "cost_rate_vs_client_model_gbp",
            "failure_events_per_100_operating_hours",
            "failure_related_cost_per_operating_hour_gbp",
        ):
            self.assertIn(metric, normalized)

    def test_site_rank_and_evidence_status_are_deterministic(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("row_number() over", normalized)
        self.assertIn(
            "partition by event_month, client_id, model order by "
            "weighted_uptime_pct desc nulls last, "
            "failure_events_per_100_operating_hours asc nulls last, "
            "failure_related_cost_per_operating_hour_gbp asc nulls last, site_id",
            normalized,
        )
        for field in (
            "benchmark_site_row_count",
            "comparable_observed_site_count",
            "comparable_operating_site_count",
            "site_comparison_rank",
        ):
            self.assertIn(field, normalized)
        for status in (
            "site_has_no_observed_duration",
            "site_has_no_operating_time",
            "single_observed_site_reference",
            "multi_site_comparison",
        ):
            self.assertIn(status, normalized)

    def test_report_keeps_downtime_and_comparison_claims_bounded(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        self.assertIn("attributed_downtime_minutes", sql)
        self.assertIn("failure_attributed_downtime_minutes", sql)
        self.assertIn("downtime_semantics_version", sql)
        for overclaim in (
            "best_site",
            "worst_site",
            "root_cause",
            "mtbf",
            "mttr",
            "reliability_score",
        ):
            with self.subTest(overclaim=overclaim):
                self.assertNotIn(overclaim, sql)

    def test_result_order_follows_month_client_model_and_rank(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "order by event_month desc, client_id, model, "
            "site_comparison_rank, site_id",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
