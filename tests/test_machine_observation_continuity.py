from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "machine_observation_continuity.sql"


class MachineObservationContinuityTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_continuity_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "machine_observation_continuity.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Machine observation continuity",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_only_the_committed_current_uptime_view(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "from main.lakehouse_demo.gold_machine_uptime",
            normalized,
        )
        for forbidden_relation in (
            "gold_machine_uptime_history",
            "gold_publication_manifest",
            "silver_machine_events",
            "bronze_machine_events",
        ):
            with self.subTest(forbidden_relation=forbidden_relation):
                self.assertNotIn(forbidden_relation, normalized)

    def test_daily_grain_and_machine_model_sequence_are_explicit(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "group by client_id, site_id, machine_id, model, event_date",
            normalized,
        )
        self.assertIn("lag(event_date) over", normalized)
        self.assertIn(
            "partition by client_id, site_id, machine_id, model "
            "order by event_date",
            normalized,
        )
        self.assertIn("where event_date is not null", normalized)

    def test_calendar_span_and_internal_gap_math_are_explicit(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "datediff(max(event_date), min(event_date)) + 1 as calendar_span_days",
            normalized,
        )
        self.assertIn(
            "datediff(event_date, previous_observed_date) "
            "as observed_date_gap_days",
            normalized,
        )
        self.assertIn(
            "then datediff(event_date, previous_observed_date) - 1",
            normalized,
        )
        for metric in (
            "consecutive_observation_pair_count",
            "observation_gap_count",
            "unobserved_days_within_span",
            "max_unobserved_gap_days",
            "avg_unobserved_gap_days",
        ):
            self.assertIn(metric, normalized)

    def test_coverage_and_duration_evidence_are_denominator_guarded(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("when calendar_span_days > 0 then", normalized)
        self.assertIn(
            "observed_day_count / calendar_span_days * 100",
            normalized,
        )
        for field in (
            "observed_day_coverage_pct",
            "no_observed_duration_day_count",
            "no_operating_time_day_count",
            "operating_hours",
            "observed_hours",
        ):
            self.assertIn(field, normalized)

    def test_status_and_scope_do_not_overclaim_missing_telemetry(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for status in (
            "no_observed_duration",
            "single_observed_day",
            "continuous_observed_dates",
            "intermittent_observed_dates",
            "observed_date_span_only",
        ):
            self.assertIn(status, sql)

        for overclaim in (
            "telemetry_completeness",
            "device_availability",
            "missing_telemetry",
            "expected_operating_days",
            "data_loss",
            "sla_breach",
        ):
            with self.subTest(overclaim=overclaim):
                self.assertNotIn(overclaim, sql)

    def test_result_order_prioritizes_lower_observed_date_coverage(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "order by observed_day_coverage_pct asc nulls last, "
            "max_unobserved_gap_days desc, "
            "no_observed_duration_day_count desc, client_id, site_id, "
            "machine_id, model",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
