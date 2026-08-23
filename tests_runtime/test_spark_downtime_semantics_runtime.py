from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from pyspark.sql import SparkSession


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_downtime_semantics import (  # noqa: E402
    SEMANTIC_VERSION,
    audit_downtime_semantics,
    with_downtime_semantics,
)


class SparkDowntimeSemanticsRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-downtime-semantics-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def frame(self, **overrides):
        row = {
            "running_minutes": 30.0,
            "idle_minutes": 20.0,
            "maintenance_minutes": 10.0,
            "observed_minutes": 60.0,
            "downtime_minutes": 15.0,
            "downtime_pct": 25.0,
        }
        row.update(overrides)
        return self.spark.createDataFrame([row])

    def test_downtime_above_observed_and_load_above_100_are_valid(self) -> None:
        frame = self.frame(downtime_minutes=120.0, downtime_pct=200.0)

        findings = audit_downtime_semantics(frame)
        row = with_downtime_semantics(frame).collect()[0]

        self.assertEqual((), findings)
        self.assertEqual(200.0, row["downtime_load_pct"])
        self.assertTrue(row["downtime_exceeds_observed"])
        self.assertEqual(SEMANTIC_VERSION, row["downtime_semantics_version"])

    def test_status_partition_above_observed_fails(self) -> None:
        frame = self.frame(
            running_minutes=40.0,
            idle_minutes=30.0,
            maintenance_minutes=10.0,
        )

        codes = {finding.code for finding in audit_downtime_semantics(frame)}

        self.assertIn("status_minutes_exceed_observed", codes)

    def test_negative_attributed_downtime_fails(self) -> None:
        frame = self.frame(downtime_minutes=-1.0, downtime_pct=-1.67)

        findings = audit_downtime_semantics(frame)

        self.assertIn(
            "negative_duration_value", {finding.code for finding in findings}
        )

    def test_legacy_formula_mismatch_fails(self) -> None:
        frame = self.frame(downtime_minutes=30.0, downtime_pct=12.0)

        findings = audit_downtime_semantics(frame)

        self.assertIn(
            "legacy_downtime_pct_formula_mismatch",
            {finding.code for finding in findings},
        )

    def test_formula_tolerance_accepts_rounding_noise(self) -> None:
        frame = self.frame(downtime_minutes=20.0, downtime_pct=33.339)

        findings = audit_downtime_semantics(frame)

        self.assertEqual((), findings)

    def test_zero_observed_with_no_downtime_has_zero_load(self) -> None:
        frame = self.frame(
            running_minutes=0.0,
            idle_minutes=0.0,
            maintenance_minutes=0.0,
            observed_minutes=0.0,
            downtime_minutes=0.0,
            downtime_pct=0.0,
        )

        row = with_downtime_semantics(frame).collect()[0]
        findings = audit_downtime_semantics(frame)

        self.assertEqual(0.0, row["downtime_load_pct"])
        self.assertFalse(row["downtime_exceeds_observed"])
        self.assertEqual((), findings)

    def test_zero_observed_with_positive_downtime_has_no_load_denominator(self) -> None:
        frame = self.frame(
            running_minutes=0.0,
            idle_minutes=0.0,
            maintenance_minutes=0.0,
            observed_minutes=0.0,
            downtime_minutes=30.0,
            downtime_pct=None,
        )

        row = with_downtime_semantics(frame).collect()[0]
        findings = audit_downtime_semantics(frame)

        self.assertIsNone(row["downtime_load_pct"])
        self.assertTrue(row["downtime_exceeds_observed"])
        self.assertEqual((), findings)


if __name__ == "__main__":
    unittest.main()
