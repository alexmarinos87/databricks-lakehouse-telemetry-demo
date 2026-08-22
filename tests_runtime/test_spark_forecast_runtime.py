from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_forecast import (  # noqa: E402
    STATUS_ACCURACY_THRESHOLD_FAILED,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_THRESHOLDS_NOT_CONFIGURED,
    STATUS_VALIDATED,
    ForecastConfig,
    build_forecast_frames,
)


class SparkForecastRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-forecast-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def uptime_frame(self, observations: list[tuple[date, int]]):
        return self.spark.createDataFrame(
            [
                {
                    "event_date": event_date,
                    "site_id": "S1",
                    "client_id": "C1",
                    "machine_id": "M1",
                    "model": "EXC-100",
                    "downtime_minutes": downtime_minutes,
                    "uptime_pct": 90.0,
                    "avg_health_score": 95.0,
                }
                for event_date, downtime_minutes in observations
            ]
        )

    def build(self, observations, config):
        return build_forecast_frames(
            self.uptime_frame(observations),
            config=config,
            forecast_run_id="job_12345",
            generated_at_utc="2026-08-21T12:00:00Z",
        )

    def test_calendar_window_does_not_treat_old_rows_as_recent_days(self) -> None:
        frames = self.build(
            [
                (date(2026, 4, 1), 10),
                (date(2026, 4, 2), 20),
                (date(2026, 4, 5), 100),
            ],
            ForecastConfig(
                baseline_window_days=2,
                forecast_horizon_days=1,
                min_validation_observations=1,
            ),
        )

        validation_dates = {
            row["event_date"]
            for row in frames["validation"].select("event_date").collect()
        }
        self.assertEqual({date(2026, 4, 2)}, validation_dates)

        forecast = frames["forecast"].collect()[0]
        self.assertEqual(date(2026, 4, 5), forecast["forecast_history_start_date"])
        self.assertEqual(1, forecast["forecast_history_day_count"])
        self.assertEqual(1, forecast["forecast_history_calendar_span_days"])
        self.assertEqual(100.0, forecast["forecast_downtime_minutes"])

    def test_calendar_window_is_stable_across_london_dst_transition(self) -> None:
        previous_timezone = self.spark.conf.get("spark.sql.session.timeZone")
        self.spark.conf.set("spark.sql.session.timeZone", "Europe/London")
        try:
            frames = self.build(
                [
                    (date(2026, 3, 28), 10),
                    (date(2026, 3, 29), 20),
                    (date(2026, 3, 30), 30),
                ],
                ForecastConfig(
                    baseline_window_days=2,
                    forecast_horizon_days=1,
                    min_validation_observations=1,
                ),
            )
            validation = {
                row["event_date"]: row["history_day_count"]
                for row in frames["validation"]
                .select("event_date", "history_day_count")
                .collect()
            }
            self.assertEqual(1, validation[date(2026, 3, 29)])
            self.assertEqual(2, validation[date(2026, 3, 30)])
        finally:
            self.spark.conf.set("spark.sql.session.timeZone", previous_timezone)

    def test_sufficient_samples_without_thresholds_are_not_validated(self) -> None:
        forecast = self.build(
            [
                (date(2026, 4, 1), 10),
                (date(2026, 4, 2), 10),
                (date(2026, 4, 3), 10),
            ],
            ForecastConfig(
                baseline_window_days=2,
                forecast_horizon_days=1,
                min_validation_observations=2,
            ),
        )["forecast"].collect()[0]

        self.assertTrue(forecast["meets_min_validation_samples"])
        self.assertFalse(forecast["thresholds_configured"])
        self.assertEqual(
            STATUS_THRESHOLDS_NOT_CONFIGURED,
            forecast["forecast_status"],
        )

    def test_configured_thresholds_validate_a_clean_baseline(self) -> None:
        forecast = self.build(
            [
                (date(2026, 4, 1), 10),
                (date(2026, 4, 2), 10),
                (date(2026, 4, 3), 10),
            ],
            ForecastConfig(
                baseline_window_days=2,
                forecast_horizon_days=1,
                min_validation_observations=2,
                max_mae_downtime_minutes=0.0,
                min_interval_coverage_pct=100.0,
            ),
        )["forecast"].collect()[0]

        self.assertEqual(0.0, forecast["mae_downtime_minutes"])
        self.assertEqual(100.0, forecast["backtest_interval_coverage_pct"])
        self.assertTrue(forecast["meets_mae_threshold"])
        self.assertTrue(forecast["meets_interval_coverage_threshold"])
        self.assertEqual(STATUS_VALIDATED, forecast["forecast_status"])

    def test_accuracy_threshold_failure_is_explicit(self) -> None:
        forecast = self.build(
            [
                (date(2026, 4, 1), 10),
                (date(2026, 4, 2), 20),
                (date(2026, 4, 3), 40),
            ],
            ForecastConfig(
                baseline_window_days=2,
                forecast_horizon_days=1,
                min_validation_observations=2,
                max_mae_downtime_minutes=5.0,
                min_interval_coverage_pct=80.0,
            ),
        )["forecast"].collect()[0]

        self.assertTrue(forecast["meets_min_validation_samples"])
        self.assertFalse(forecast["meets_mae_threshold"])
        self.assertEqual(
            STATUS_ACCURACY_THRESHOLD_FAILED,
            forecast["forecast_status"],
        )

    def test_insufficient_history_takes_priority_over_threshold_results(self) -> None:
        forecast = self.build(
            [(date(2026, 4, 1), 10)],
            ForecastConfig(
                baseline_window_days=2,
                forecast_horizon_days=1,
                min_validation_observations=2,
                max_mae_downtime_minutes=100.0,
                min_interval_coverage_pct=0.0,
            ),
        )["forecast"].collect()[0]

        self.assertEqual(0, forecast["validation_observation_count"])
        self.assertEqual(
            STATUS_INSUFFICIENT_HISTORY,
            forecast["forecast_status"],
        )

    def test_partial_threshold_configuration_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be configured together"):
            ForecastConfig(
                baseline_window_days=2,
                forecast_horizon_days=1,
                min_validation_observations=2,
                max_mae_downtime_minutes=10.0,
            )

    def test_run_context_is_bounded_and_utc_shaped(self) -> None:
        config = ForecastConfig(
            baseline_window_days=2,
            forecast_horizon_days=1,
            min_validation_observations=1,
        )
        frame = self.uptime_frame([(date(2026, 4, 1), 10)])

        with self.assertRaisesRegex(ValueError, "forecast_run_id"):
            build_forecast_frames(
                frame,
                config=config,
                forecast_run_id="unsafe run id",
                generated_at_utc="2026-08-21T12:00:00Z",
            )
        with self.assertRaisesRegex(ValueError, "generated_at_utc"):
            build_forecast_frames(
                frame,
                config=config,
                forecast_run_id="job_12345",
                generated_at_utc="2026-08-21 12:00:00",
            )


if __name__ == "__main__":
    unittest.main()
