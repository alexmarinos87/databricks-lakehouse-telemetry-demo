from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "machine_fault_recurrence.sql"


class MachineFaultRecurrenceTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_recurrence_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "machine_fault_recurrence.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Machine fault recurrence",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_committed_current_warehouse_relations(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        for relation in (
            "fact_machine_failure_event",
            "dim_date",
            "dim_client",
            "dim_site",
            "dim_machine",
            "dim_model",
            "dim_fault",
        ):
            with self.subTest(relation=relation):
                self.assertIn(f"main.lakehouse_demo.{relation}", normalized)

        for forbidden_relation in (
            "fact_machine_failure_event_history",
            "warehouse_publication_manifest",
            "gold_failure_events_history",
            "gold_publication_manifest",
        ):
            with self.subTest(forbidden_relation=forbidden_relation):
                self.assertNotIn(forbidden_relation, normalized)

    def test_event_summary_is_at_machine_fault_assignment_grain(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())
        grain = (
            "group by client_id, site_id, machine_id, model, fault_code, "
            "severity, severity_rank"
        )

        self.assertGreaterEqual(normalized.count(grain), 2)
        for measure in (
            "first_observed_failure_date",
            "latest_observed_failure_date",
            "first_observed_failure_at_utc",
            "latest_observed_failure_at_utc",
            "observed_failure_event_count",
            "observed_failure_identity_count",
            "observed_failure_day_count",
            "observed_failure_month_count",
            "attributed_downtime_minutes",
            "failure_related_cost_gbp",
        ):
            with self.subTest(measure=measure):
                self.assertIn(measure, normalized)

    def test_recurrence_intervals_use_distinct_observed_failure_dates(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("failure_dates as", normalized)
        self.assertIn(
            "select distinct client_id, site_id, machine_id, model, fault_code, "
            "severity, severity_rank, event_date from failure_events",
            normalized,
        )
        self.assertIn("lag(event_date) over", normalized)
        self.assertIn(
            "partition by client_id, site_id, machine_id, model, fault_code, "
            "severity, severity_rank order by event_date",
            normalized,
        )
        self.assertIn(
            "datediff(event_date, previous_observed_failure_date)",
            normalized,
        )
        for metric in (
            "recurrence_interval_observation_count",
            "min_days_between_observed_failure_days",
            "avg_days_between_observed_failure_days",
            "max_days_between_observed_failure_days",
        ):
            self.assertIn(metric, normalized)

    def test_repeat_and_per_event_measures_are_explicitly_guarded(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "when events.observed_failure_event_count > 0 then "
            "events.observed_failure_event_count - 1",
            normalized,
        )
        self.assertGreaterEqual(
            normalized.count("when events.observed_failure_event_count > 0 then round("),
            2,
        )
        for measure in (
            "repeat_observed_failure_event_count",
            "avg_attributed_downtime_per_failure_event_minutes",
            "avg_failure_related_cost_per_event_gbp",
            "observed_failure_calendar_span_days",
        ):
            self.assertIn(measure, normalized)
        self.assertIn("events.model <=> gaps.model", normalized)

    def test_statuses_describe_observed_recurrence_without_prediction_claims(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for status in (
            "single_observed_failure_event",
            "repeat_events_on_one_observed_day",
            "repeat_events_across_observed_days",
            "repeat_events_across_observed_months",
            "observed_failure_events_only",
        ):
            self.assertIn(status, sql)

        for overclaim in (
            "next_failure",
            "failure_probability",
            "predictive_failure",
            "root_cause",
            "caused_by",
            "mtbf",
            "mttr",
        ):
            with self.subTest(overclaim=overclaim):
                self.assertNotIn(overclaim, sql)

    def test_recurrence_rank_and_output_order_are_deterministic(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("row_number() over", normalized)
        self.assertIn(
            "partition by client_id, site_id, model order by "
            "observed_failure_event_count desc, observed_failure_day_count desc, "
            "attributed_downtime_minutes desc, failure_related_cost_gbp desc, "
            "severity_rank desc, machine_id, fault_code, severity",
            normalized,
        )
        self.assertIn("observed_recurrence_rank", normalized)
        self.assertIn(
            "order by client_id, site_id, model, observed_recurrence_rank, "
            "machine_id, fault_code, severity",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
