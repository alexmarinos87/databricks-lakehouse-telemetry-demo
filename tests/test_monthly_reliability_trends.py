from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "monthly_reliability_trends.sql"


class MonthlyReliabilityTrendsTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_trend_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "monthly_reliability_trends.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Monthly reliability trends",
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
            "from main.lakehouse_demo.gold_machine_uptime",
            normalized,
        )
        self.assertIn(
            "from main.lakehouse_demo.gold_failure_events",
            normalized,
        )
        self.assertGreaterEqual(
            normalized.count("date_trunc('month', cast(event_date as timestamp))"),
            4,
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
            with self.subTest(forbidden_relation=forbidden_relation):
                self.assertNotIn(forbidden_relation, normalized)

    def test_complete_month_keys_and_model_safe_attribution_are_explicit(self):
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
        for key_join in (
            "keys.event_month = uptime.event_month",
            "keys.client_id = uptime.client_id",
            "keys.site_id = uptime.site_id",
            "keys.event_month = failures.event_month",
            "keys.client_id = failures.client_id",
            "keys.site_id = failures.site_id",
        ):
            with self.subTest(key_join=key_join):
                self.assertIn(key_join, normalized)

    def test_weighted_uptime_and_failure_measures_are_denominator_guarded(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for required in (
            "when coalesce(uptime.observed_minutes, 0) > 0",
            "uptime.running_minutes / uptime.observed_minutes * 100",
            "weighted_uptime_pct",
            "operating_hours",
            "observed_hours",
            "failure_event_count",
            "affected_machine_count",
            "failure_attributed_downtime_minutes",
            "failure_related_cost_gbp",
            "downtime_semantics_version",
        ):
            with self.subTest(required=required):
                self.assertIn(required, sql)

    def test_deltas_are_emitted_only_for_consecutive_months(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertGreaterEqual(normalized.count("lag("), 5)
        self.assertGreaterEqual(
            normalized.count(
                "partition by client_id, site_id, model order by event_month"
            ),
            5,
        )
        for status in (
            "no_observed_duration",
            "first_observed_month",
            "non_consecutive_history",
            "consecutive_comparison",
        ):
            self.assertIn(status, normalized)
        self.assertIn(
            "add_months(cast(previous_event_month as date), 1) "
            "= cast(event_month as date)",
            normalized,
        )
        for delta in (
            "weighted_uptime_pct_change",
            "failure_event_count_change",
            "attributed_downtime_minutes_change",
            "failure_related_cost_change_gbp",
        ):
            self.assertIn(delta, normalized)

    def test_semantic_and_ordering_boundaries_are_not_overclaimed(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        self.assertIn("attributed_downtime", sql)
        self.assertNotIn("mtbf", sql)
        self.assertNotIn("mttr", sql)
        self.assertNotIn("improving", sql)
        self.assertNotIn("deteriorating", sql)
        self.assertIn("event_month desc", sql)
        self.assertIn("failure_event_count desc", sql)
        self.assertIn("attributed_downtime_minutes desc", sql)


if __name__ == "__main__":
    unittest.main()
