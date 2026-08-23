from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_quality import (  # noqa: E402
    QualityCheckResult,
    evaluate_quality_tables,
    quality_results_dataframe,
    summarize_quality_results,
)


class SparkQualityRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-quality-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

        cls.silver = cls.spark.createDataFrame(
            [
                {
                    "event_id": "E1",
                    "machine_id": "M1",
                    "event_ts_utc": datetime(2026, 4, 1, 6),
                    "site_id": "S1",
                    "client_id": "C1",
                    "duration_minutes": 60,
                    "downtime_minutes": 0,
                    "maintenance_cost_gbp": 0.0,
                    "part_quantity": 0,
                    "fuel_level_pct": 80.0,
                },
                {
                    "event_id": "E2",
                    "machine_id": "M1",
                    "event_ts_utc": datetime(2026, 4, 1, 7),
                    "site_id": "S1",
                    "client_id": "C1",
                    "duration_minutes": 30,
                    "downtime_minutes": 10,
                    "maintenance_cost_gbp": 20.0,
                    "part_quantity": 1,
                    "fuel_level_pct": 70.0,
                },
            ]
        ).cache()
        cls.uptime = cls.spark.createDataFrame(
            [
                {
                    "date_key": 20260401,
                    "client_key": 1,
                    "machine_key": 10,
                    "model_key": 100,
                    "site_key": 1000,
                    "running_minutes": 60,
                    "idle_minutes": 20,
                    "maintenance_minutes": 10,
                    "downtime_minutes": 10,
                    "observed_minutes": 90,
                    "uptime_pct": 66.67,
                    "idle_pct": 22.22,
                    "maintenance_pct": 11.11,
                    "downtime_pct": 11.11,
                    "downtime_load_pct": 11.11,
                    "downtime_exceeds_observed": False,
                    "downtime_semantics_version": "attributed_incident_v1",
                }
            ]
        ).cache()
        cls.failure = cls.spark.createDataFrame(
            [
                {
                    "event_id": "E2",
                    "date_key": 20260401,
                    "client_key": 1,
                    "machine_key": 10,
                    "model_key": 100,
                    "site_key": 1000,
                    "fault_key": 10000,
                    "failure_event_count": 1,
                    "downtime_minutes": 10,
                    "maintenance_cost_gbp": 20.0,
                    "part_quantity": 1,
                }
            ]
        ).cache()
        for dataframe in (cls.silver, cls.uptime, cls.failure):
            dataframe.count()

    @classmethod
    def tearDownClass(cls) -> None:
        for dataframe in (cls.silver, cls.uptime, cls.failure):
            dataframe.unpersist()
        cls.spark.stop()

    def _evaluate(self, **overrides):
        frames = {
            "silver": self.silver,
            "fact_machine_uptime_daily": self.uptime,
            "fact_machine_failure_event": self.failure,
        }
        frames.update(overrides)
        return evaluate_quality_tables(frames, expected_tables=tuple(frames))

    def test_clean_medallion_and_warehouse_checks_pass(self) -> None:
        results = self._evaluate()
        self.assertFalse(any(result.status == "fail" for result in results))
        self.assertIn(
            QualityCheckResult(
                "uptime_fact_grain_unique",
                "pass",
                "error",
                "Warehouse fact grain is unique",
                0,
            ),
            results,
        )
        self.assertIn(
            QualityCheckResult(
                "uptime_fact_downtime_semantics_valid",
                "pass",
                "error",
                "Attributed downtime fields match the accepted semantic contract",
                0,
            ),
            results,
        )

    def test_unavailable_table_is_recorded_without_provider_diagnostics(self) -> None:
        results = evaluate_quality_tables(
            {},
            unavailable_tables={"silver": "main.demo.silver"},
            expected_tables=("silver",),
        )
        self.assertEqual(
            (
                QualityCheckResult(
                    "silver_table_readable",
                    "fail",
                    "error",
                    "Required table could not be read",
                    1,
                ),
            ),
            results,
        )
        self.assertNotIn("main.demo.silver", results[0].detail)

    def test_duplicate_grain_and_null_dimension_key_are_detected(self) -> None:
        results = self._evaluate(
            fact_machine_uptime_daily=self.uptime.unionByName(self.uptime),
            fact_machine_failure_event=self.failure.withColumn(
                "fault_key", F.lit(None).cast("long")
            ),
        )
        self.assertIn(
            QualityCheckResult(
                "uptime_fact_grain_unique",
                "fail",
                "error",
                "Warehouse fact contains duplicate grain rows",
                1,
            ),
            results,
        )
        self.assertIn(
            QualityCheckResult(
                "failure_fact_dimension_keys_present",
                "fail",
                "error",
                "Warehouse fact contains null dimension keys",
                1,
            ),
            results,
        )

    def test_high_attributed_downtime_load_is_valid_when_evidence_reconciles(self) -> None:
        high_load = (
            self.uptime.withColumn("downtime_minutes", F.lit(120))
            .withColumn("downtime_pct", F.lit(133.33))
            .withColumn("downtime_load_pct", F.lit(133.33))
            .withColumn("downtime_exceeds_observed", F.lit(True))
        )
        results = self._evaluate(fact_machine_uptime_daily=high_load)
        semantic = next(
            result
            for result in results
            if result.check_name == "uptime_fact_downtime_semantics_valid"
        )

        self.assertEqual(
            ("pass", "error", 0),
            (semantic.status, semantic.severity, semantic.observed_count),
        )
        self.assertFalse(any(result.status == "fail" for result in results))

    def test_corrupted_downtime_load_is_an_error(self) -> None:
        corrupted = self.uptime.withColumn("downtime_load_pct", F.lit(999.0))
        results = self._evaluate(fact_machine_uptime_daily=corrupted)
        semantic = next(
            result
            for result in results
            if result.check_name == "uptime_fact_downtime_semantics_valid"
        )

        self.assertEqual("fail", semantic.status)
        self.assertEqual("error", semantic.severity)
        self.assertGreaterEqual(semantic.observed_count, 1)

    def test_detailed_and_summary_frames_share_run_identity(self) -> None:
        detailed = quality_results_dataframe(
            self.spark,
            (
                QualityCheckResult("pass_check", "pass", "error", "passed", 0),
                QualityCheckResult("error_check", "fail", "error", "failed", 2),
                QualityCheckResult("warning_check", "fail", "warning", "review", 1),
            ),
            quality_run_id="quality-run-1",
            checked_at=datetime(2026, 8, 21, 10, 30),
        )
        summary = summarize_quality_results(detailed).collect()[0].asDict()
        self.assertEqual("quality-run-1", summary["quality_run_id"])
        self.assertEqual(3, summary["check_count"])
        self.assertEqual(1, summary["passed_check_count"])
        self.assertEqual(2, summary["failed_check_count"])
        self.assertEqual(1, summary["failed_error_check_count"])
        self.assertEqual(1, summary["failed_warning_check_count"])
        self.assertFalse(summary["all_error_checks_passed"])


if __name__ == "__main__":
    unittest.main()
