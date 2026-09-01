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
    / "machine_part_usage_recurrence.sql"
)


def normalized_sql() -> str:
    return " ".join(QUERY.read_text(encoding="utf-8").lower().split())


class MachinePartUsageRecurrenceTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_part_recurrence_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "machine_part_usage_recurrence.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Machine part usage recurrence",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_committed_current_warehouse_relations(self):
        normalized = normalized_sql()

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
            "gold_parts_usage_history",
            "gold_publication_manifest",
        ):
            with self.subTest(forbidden_relation=forbidden_relation):
                self.assertNotIn(forbidden_relation, normalized)

    def test_part_records_are_filtered_and_summarized_at_machine_fault_part_grain(self):
        normalized = normalized_sql()
        grain = (
            "group by client_id, site_id, machine_id, model, fault_code, "
            "severity, severity_rank, part_code"
        )

        self.assertIn("failure_fact.part_code is not null", normalized)
        self.assertIn("upper(trim(failure_fact.part_code)) <> 'none'", normalized)
        self.assertIn(
            "coalesce(failure_fact.part_quantity, 0) > 0",
            normalized,
        )
        self.assertGreaterEqual(normalized.count(grain), 2)

        for measure in (
            "first_observed_part_use_date",
            "latest_observed_part_use_date",
            "first_observed_part_use_at_utc",
            "latest_observed_part_use_at_utc",
            "observed_part_event_count",
            "observed_part_identity_count",
            "observed_part_day_count",
            "observed_part_month_count",
            "total_recorded_part_quantity",
            "associated_attributed_downtime_minutes",
            "associated_failure_cost_gbp",
        ):
            with self.subTest(measure=measure):
                self.assertIn(measure, normalized)

    def test_recurrence_intervals_use_distinct_observed_part_dates(self):
        normalized = normalized_sql()

        self.assertIn("usage_dates as", normalized)
        self.assertIn(
            "select distinct client_id, site_id, machine_id, model, "
            "fault_code, severity, severity_rank, part_code, event_date "
            "from part_events",
            normalized,
        )
        self.assertIn("lag(event_date) over", normalized)
        self.assertIn(
            "partition by client_id, site_id, machine_id, model, "
            "fault_code, severity, severity_rank, part_code "
            "order by event_date",
            normalized,
        )
        self.assertIn(
            "datediff(event_date, previous_observed_part_use_date)",
            normalized,
        )
        for metric in (
            "recurrence_interval_observation_count",
            "min_days_between_observed_part_use_dates",
            "avg_days_between_observed_part_use_dates",
            "max_days_between_observed_part_use_dates",
        ):
            self.assertIn(metric, normalized)

    def test_repeat_quantity_cost_and_downtime_measures_are_guarded(self):
        normalized = normalized_sql()

        self.assertIn(
            "when observed_part_event_count > 0 then "
            "observed_part_event_count - 1",
            normalized,
        )
        self.assertGreaterEqual(
            normalized.count("when observed_part_event_count > 0 then"),
            4,
        )
        for measure in (
            "repeat_observed_part_event_count",
            "avg_recorded_part_quantity_per_event",
            "avg_associated_downtime_per_part_event_minutes",
            "avg_associated_failure_cost_per_part_event_gbp",
            "observed_part_use_calendar_span_days",
        ):
            self.assertIn(measure, normalized)
        self.assertIn("usage.model <=> intervals.model", normalized)

    def test_statuses_describe_observed_part_records_without_causal_claims(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for status in (
            "single_observed_part_event",
            "repeat_part_events_on_one_observed_day",
            "repeat_part_events_across_observed_days",
            "repeat_part_events_across_observed_months",
            "observed_failure_part_records_only",
        ):
            self.assertIn(status, sql)

        for overclaim in (
            "next_part",
            "part_demand_forecast",
            "failure_probability",
            "root_cause",
            "caused_by",
            "preventive_replacement",
            "mtbf",
            "mttr",
        ):
            with self.subTest(overclaim=overclaim):
                self.assertNotIn(overclaim, sql)

    def test_recurrence_rank_and_result_order_are_deterministic(self):
        normalized = normalized_sql()

        self.assertIn("row_number() over", normalized)
        self.assertIn(
            "partition by client_id, site_id, model order by "
            "total_recorded_part_quantity desc, "
            "observed_part_event_count desc, "
            "associated_failure_cost_gbp desc, severity_rank desc, "
            "machine_id, fault_code, part_code, severity",
            normalized,
        )
        self.assertIn("observed_part_usage_rank", normalized)
        self.assertIn(
            "order by client_id, site_id, model, observed_part_usage_rank, "
            "machine_id, fault_code, part_code, severity",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
