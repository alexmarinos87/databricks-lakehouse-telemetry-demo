from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sql" / "reporting_assets" / "manifest.json"
QUERY = ROOT / "sql" / "reporting_assets" / "forecast_validation_diagnostics.sql"


class ForecastValidationDiagnosticsTest(unittest.TestCase):
    def test_manifest_registers_one_resolved_diagnostics_asset(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest
            if item.get("file") == "forecast_validation_diagnostics.sql"
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(
            "Lakehouse Demo - Forecast validation diagnostics",
            matches[0]["display_name"],
        )
        self.assertTrue(QUERY.is_file())
        self.assertEqual(
            len(manifest),
            len({item["display_name"] for item in manifest}),
        )
        self.assertEqual(len(manifest), len({item["file"] for item in manifest}))

    def test_query_uses_the_committed_current_validation_view(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "from main.lakehouse_demo.gold_downtime_forecast_validation",
            normalized,
        )
        for forbidden_relation in (
            "gold_downtime_forecast_validation_history",
            "gold_downtime_forecast_history",
            "gold_downtime_forecast_publication_manifest",
        ):
            with self.subTest(forbidden_relation=forbidden_relation):
                self.assertNotIn(forbidden_relation, normalized)

        for grain_column in (
            "forecast_run_id",
            "client_id",
            "site_id",
            "model",
        ):
            self.assertIn(grain_column, normalized)

    def test_error_metrics_and_percentage_coverage_are_explicitly_guarded(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        for metric in (
            "mean_error_minutes",
            "mae_downtime_minutes",
            "rmse_downtime_minutes",
            "mape_pct",
            "validation_interval_coverage_pct",
            "under_forecast_observation_count",
            "over_forecast_observation_count",
            "exact_forecast_observation_count",
        ):
            with self.subTest(metric=metric):
                self.assertIn(metric, normalized)

        self.assertIn(
            "when absolute_percentage_error is not null then 1 else 0 end",
            normalized,
        )
        self.assertIn("when count(*) > 0", normalized)
        self.assertIn(
            "when covered_by_validation_interval then 1.0 else 0.0 end",
            normalized,
        )

    def test_largest_error_observation_is_selected_deterministically(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn("row_number() over", normalized)
        self.assertIn(
            "partition by forecast_run_id, client_id, site_id, model "
            "order by absolute_error_minutes desc, event_date desc",
            normalized,
        )
        self.assertIn("where absolute_error_rank = 1", normalized)
        self.assertIn("metrics.model <=> largest.model", normalized)
        for field in (
            "largest_absolute_error_date",
            "largest_absolute_error_minutes",
            "largest_error_residual_minutes",
            "largest_error_covered_by_interval",
        ):
            self.assertIn(field, normalized)

    def test_bias_status_describes_direction_without_accuracy_overclaim(self):
        sql = QUERY.read_text(encoding="utf-8").lower()

        for status in (
            "exact_validation_observations",
            "under_forecast_bias_observed",
            "over_forecast_bias_observed",
            "balanced_mean_error",
        ):
            self.assertIn(status, sql)

        self.assertIn("actual_downtime_minutes", sql)
        self.assertIn("forecast_downtime_minutes", sql)
        self.assertNotIn("root_cause", sql)
        self.assertNotIn("production_ready", sql)
        self.assertNotIn("model_is_accurate", sql)

    def test_result_order_prioritizes_larger_observed_errors(self):
        normalized = " ".join(QUERY.read_text(encoding="utf-8").lower().split())

        self.assertIn(
            "order by metrics.mae_downtime_minutes desc, "
            "abs(metrics.mean_error_minutes) desc, metrics.client_id, "
            "metrics.site_id, metrics.model",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
