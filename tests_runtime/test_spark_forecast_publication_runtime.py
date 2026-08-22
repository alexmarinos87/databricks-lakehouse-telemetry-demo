from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_forecast_publication import (  # noqa: E402
    STATE_COMMITTED,
    STATE_STARTED,
    audit_publication_run,
    build_publication_manifest,
    latest_committed_run_id,
    select_latest_committed_frames,
)


class SparkForecastPublicationRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-forecast-publication-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def _frames(
        self,
        run_id: str,
        *,
        forecast_value: float,
        validation_values: tuple[float, ...] = (8.0, 10.0),
    ):
        forecast = self.spark.createDataFrame(
            [
                {
                    "forecast_run_id": run_id,
                    "model_name": "rolling_mean_baseline",
                    "window_semantics": "calendar_days",
                    "baseline_window_days": 2,
                    "forecast_date": date(2026, 4, 4),
                    "site_id": "S1",
                    "client_id": "C1",
                    "model": "EXC-100",
                    "forecast_downtime_minutes": forecast_value,
                }
            ]
        )
        validation = self.spark.createDataFrame(
            [
                {
                    "forecast_run_id": run_id,
                    "model_name": "rolling_mean_baseline",
                    "window_semantics": "calendar_days",
                    "baseline_window_days": 2,
                    "event_date": date(2026, 4, index + 1),
                    "site_id": "S1",
                    "client_id": "C1",
                    "model": "EXC-100",
                    "actual_downtime_minutes": value,
                }
                for index, value in enumerate(validation_values)
            ]
        )
        return validation, forecast

    def _manifest(
        self,
        run_id: str,
        *,
        forecast_value: float,
        state: str,
        started_at: str,
        completed_at: str | None,
    ):
        validation, forecast = self._frames(
            run_id,
            forecast_value=forecast_value,
        )
        manifest = build_publication_manifest(
            validation,
            forecast,
            publication_state=state,
            publication_started_at_utc=started_at,
            forecast_generated_at_utc=started_at,
            publication_completed_at_utc=completed_at,
        )
        return manifest, validation, forecast

    def test_started_run_does_not_replace_latest_committed_run(self) -> None:
        committed, validation_one, forecast_one = self._manifest(
            "run_1",
            forecast_value=10.0,
            state=STATE_COMMITTED,
            started_at="2026-04-01T10:00:00Z",
            completed_at="2026-04-01T10:01:00Z",
        )
        started, validation_two, forecast_two = self._manifest(
            "run_2",
            forecast_value=99.0,
            state=STATE_STARTED,
            started_at="2026-04-02T10:00:00Z",
            completed_at=None,
        )

        selected = select_latest_committed_frames(
            manifest=committed.unionByName(started),
            forecast_history=forecast_one.unionByName(forecast_two),
            validation_history=validation_one.unionByName(validation_two),
        )

        self.assertEqual("run_1", selected["forecast_run_id"])
        self.assertEqual(
            [10.0],
            [
                row["forecast_downtime_minutes"]
                for row in selected["forecast"].collect()
            ],
        )

    def test_latest_committed_completion_time_controls_visibility(self) -> None:
        first, validation_one, forecast_one = self._manifest(
            "run_1",
            forecast_value=10.0,
            state=STATE_COMMITTED,
            started_at="2026-04-01T10:00:00Z",
            completed_at="2026-04-01T10:01:00Z",
        )
        second, validation_two, forecast_two = self._manifest(
            "run_2",
            forecast_value=20.0,
            state=STATE_COMMITTED,
            started_at="2026-04-02T10:00:00Z",
            completed_at="2026-04-02T10:01:00Z",
        )

        manifest = first.unionByName(second)
        self.assertEqual("run_2", latest_committed_run_id(manifest))
        selected = select_latest_committed_frames(
            manifest=manifest,
            forecast_history=forecast_one.unionByName(forecast_two),
            validation_history=validation_one.unionByName(validation_two),
        )
        self.assertEqual("run_2", selected["forecast_run_id"])

    def test_retry_fingerprint_is_independent_of_row_order(self) -> None:
        validation, forecast = self._frames(
            "retry_run",
            forecast_value=10.0,
            validation_values=(8.0, 10.0, 12.0),
        )
        first = build_publication_manifest(
            validation,
            forecast,
            publication_state=STATE_STARTED,
            publication_started_at_utc="2026-04-03T10:00:00Z",
            forecast_generated_at_utc="2026-04-03T10:00:00Z",
        ).collect()[0]
        second = build_publication_manifest(
            validation.orderBy(F.col("event_date").desc()),
            forecast.repartition(2),
            publication_state=STATE_STARTED,
            publication_started_at_utc="2026-04-03T10:00:00Z",
            forecast_generated_at_utc="2026-04-03T10:00:00Z",
        ).collect()[0]

        for column in (
            "forecast_row_count",
            "validation_row_count",
            "forecast_schema_sha256",
            "validation_schema_sha256",
            "forecast_payload_sha256",
            "validation_payload_sha256",
        ):
            self.assertEqual(first[column], second[column])

    def test_missing_history_row_is_detected_before_commit(self) -> None:
        manifest, validation, forecast = self._manifest(
            "run_partial",
            forecast_value=10.0,
            state=STATE_COMMITTED,
            started_at="2026-04-04T10:00:00Z",
            completed_at="2026-04-04T10:01:00Z",
        )
        findings = audit_publication_run(
            manifest=manifest,
            forecast_history=forecast,
            validation_history=validation.limit(1),
            forecast_run_id="run_partial",
        )

        self.assertEqual(
            {
                ("history_row_count_mismatch", "validation_history"),
                ("history_payload_mismatch", "validation_history"),
            },
            {(finding.code, finding.dataset) for finding in findings},
        )

    def test_same_count_payload_corruption_is_detected(self) -> None:
        manifest, validation, forecast = self._manifest(
            "run_corrupt",
            forecast_value=10.0,
            state=STATE_COMMITTED,
            started_at="2026-04-05T10:00:00Z",
            completed_at="2026-04-05T10:01:00Z",
        )
        corrupted_forecast = forecast.withColumn(
            "forecast_downtime_minutes",
            F.col("forecast_downtime_minutes") + F.lit(1.0),
        )
        findings = audit_publication_run(
            manifest=manifest,
            forecast_history=corrupted_forecast,
            validation_history=validation,
            forecast_run_id="run_corrupt",
        )

        self.assertEqual(
            {("history_payload_mismatch", "forecast_history")},
            {(finding.code, finding.dataset) for finding in findings},
        )

    def test_later_history_columns_do_not_invalidate_recorded_run_schema(self) -> None:
        manifest, validation, forecast = self._manifest(
            "run_schema",
            forecast_value=10.0,
            state=STATE_COMMITTED,
            started_at="2026-04-06T10:00:00Z",
            completed_at="2026-04-06T10:01:00Z",
        )
        evolved_forecast = forecast.withColumn(
            "future_optional_column",
            F.lit(None).cast("string"),
        )
        evolved_validation = validation.withColumn(
            "future_optional_column",
            F.lit(None).cast("string"),
        )

        self.assertEqual(
            (),
            audit_publication_run(
                manifest=manifest,
                forecast_history=evolved_forecast,
                validation_history=evolved_validation,
                forecast_run_id="run_schema",
            ),
        )

    def test_duplicate_manifest_run_fails_closed(self) -> None:
        manifest, validation, forecast = self._manifest(
            "run_duplicate",
            forecast_value=10.0,
            state=STATE_COMMITTED,
            started_at="2026-04-07T10:00:00Z",
            completed_at="2026-04-07T10:01:00Z",
        )

        with self.assertRaisesRegex(ValueError, "duplicate forecast_run_id"):
            latest_committed_run_id(manifest.unionByName(manifest))

        findings = audit_publication_run(
            manifest=manifest.unionByName(manifest),
            forecast_history=forecast,
            validation_history=validation,
            forecast_run_id="run_duplicate",
        )
        self.assertEqual("duplicate_manifest_run", findings[0].code)

    def test_empty_validation_history_is_a_valid_zero_row_vintage(self) -> None:
        validation, forecast = self._frames(
            "run_no_backtest",
            forecast_value=10.0,
        )
        empty_validation = validation.limit(0)
        manifest = build_publication_manifest(
            empty_validation,
            forecast,
            publication_state=STATE_COMMITTED,
            publication_started_at_utc="2026-04-08T09:00:00Z",
            forecast_generated_at_utc="2026-04-08T09:00:00Z",
            publication_completed_at_utc="2026-04-08T09:01:00Z",
        )

        self.assertEqual(
            (),
            audit_publication_run(
                manifest=manifest,
                forecast_history=forecast,
                validation_history=empty_validation,
                forecast_run_id="run_no_backtest",
            ),
        )
        self.assertEqual(0, manifest.collect()[0]["validation_row_count"])

    def test_manifest_without_commit_has_no_current_run(self) -> None:
        manifest, _, _ = self._manifest(
            "run_started",
            forecast_value=10.0,
            state=STATE_STARTED,
            started_at="2026-04-08T10:00:00Z",
            completed_at=None,
        )
        self.assertIsNone(latest_committed_run_id(manifest))


if __name__ == "__main__":
    unittest.main()
