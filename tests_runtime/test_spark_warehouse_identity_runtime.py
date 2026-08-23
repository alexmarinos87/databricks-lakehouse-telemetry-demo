from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_warehouse import (  # noqa: E402
    FAILURE_FACT,
    UPTIME_FACT,
    WarehouseFinding,
    build_warehouse_frames,
)
from lakehouse_demo.warehouse_measures import audit_warehouse_measures  # noqa: E402
from lakehouse_demo.warehouse_publication import audit_warehouse_publication  # noqa: E402


class WarehouseIdentityRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-warehouse-publication-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

        cls.gold_uptime = cls.spark.createDataFrame([
            {
                "event_date": date(2026, 4, 1), "site_id": "S1",
                "client_id": "C1", "machine_id": "M1", "model": "EXC-100",
                "running_minutes": 50, "idle_minutes": 10,
                "maintenance_minutes": 0, "downtime_minutes": 0,
                "observed_minutes": 60, "uptime_pct": 83.33,
                "avg_health_score": 95.0,
            },
            {
                "event_date": date(2026, 4, 2), "site_id": "S2",
                "client_id": "C1", "machine_id": "M2", "model": "EXC-200",
                "running_minutes": 30, "idle_minutes": 0,
                "maintenance_minutes": 15, "downtime_minutes": 15,
                "observed_minutes": 45, "uptime_pct": 66.67,
                "avg_health_score": 55.0,
            },
        ]).cache()
        cls.gold_failures = cls.spark.createDataFrame([{
            "event_id": "E2", "event_date": date(2026, 4, 2),
            "event_ts_utc": datetime(2026, 4, 2, 9, 30, 0),
            "site_id": "S2", "client_id": "C1", "machine_id": "M2",
            "model": "EXC-200", "fault_code": "HYD-01", "severity": "high",
            "temperature_c": 95.0, "vibration_mm_s": 7.5,
            "downtime_minutes": 15, "maintenance_cost_gbp": 250.0,
            "part_code": "PUMP", "part_quantity": 1,
        }]).cache()
        cls.frames = {
            name: dataframe.cache()
            for name, dataframe in build_warehouse_frames(
                cls.gold_uptime, cls.gold_failures
            ).items()
        }
        cls.gold_uptime.count(); cls.gold_failures.count()
        for dataframe in cls.frames.values(): dataframe.count()

    @classmethod
    def tearDownClass(cls) -> None:
        for dataframe in [cls.gold_uptime, cls.gold_failures, *cls.frames.values()]:
            dataframe.unpersist()
        cls.spark.stop()

    def publication_findings(self, frames):
        return audit_warehouse_publication(
            gold_uptime=self.gold_uptime,
            gold_failures=self.gold_failures,
            warehouse_frames=frames,
        )

    def measure_findings(self, frames):
        return audit_warehouse_measures(
            gold_uptime=self.gold_uptime,
            gold_failures=self.gold_failures,
            warehouse_frames=frames,
        )

    def test_clean_warehouse_passes_composite_publication_audit(self) -> None:
        self.assertEqual((), self.publication_findings(self.frames))

    def test_equal_counts_and_valid_foreign_keys_do_not_hide_wrong_uptime_identity(self) -> None:
        machine_keys = {
            row["machine_id"]: row["machine_key"]
            for row in self.frames["dim_machine"].select("machine_id", "machine_key").collect()
        }
        corrupted = self.frames[UPTIME_FACT].withColumn(
            "machine_key",
            F.when(F.col("event_date") == F.lit(date(2026, 4, 1)),
                   F.lit(machine_keys["M2"])).otherwise(F.col("machine_key")),
        )
        frames = dict(self.frames); frames[UPTIME_FACT] = corrupted
        self.assertEqual({
            WarehouseFinding("missing_fact_identity", UPTIME_FACT, 1),
            WarehouseFinding("unexpected_fact_identity", UPTIME_FACT, 1),
        }, set(self.publication_findings(frames)))

    def test_equal_counts_do_not_hide_wrong_failure_event_identity(self) -> None:
        frames = dict(self.frames)
        frames[FAILURE_FACT] = self.frames[FAILURE_FACT].withColumn(
            "event_id", F.lit("E_UNEXPECTED")
        )
        self.assertEqual({
            WarehouseFinding("missing_fact_identity", FAILURE_FACT, 1),
            WarehouseFinding("unexpected_fact_identity", FAILURE_FACT, 1),
        }, set(self.publication_findings(frames)))

    def test_wrong_but_valid_date_key_is_resolved_through_dim_date(self) -> None:
        date_keys = {
            row["date_day"]: row["date_key"]
            for row in self.frames["dim_date"].select("date_day", "date_key").collect()
        }
        corrupted = self.frames[UPTIME_FACT].withColumn(
            "date_key",
            F.when(F.col("event_date") == F.lit(date(2026, 4, 1)),
                   F.lit(date_keys[date(2026, 4, 2)])).otherwise(F.col("date_key")),
        )
        frames = dict(self.frames); frames[UPTIME_FACT] = corrupted
        self.assertEqual({
            WarehouseFinding("missing_fact_identity", UPTIME_FACT, 1),
            WarehouseFinding("unexpected_fact_identity", UPTIME_FACT, 1),
        }, set(self.publication_findings(frames)))

    def test_redundant_fact_event_date_must_match_the_resolved_identity(self) -> None:
        frames = dict(self.frames)
        frames[UPTIME_FACT] = self.frames[UPTIME_FACT].withColumn(
            "event_date",
            F.when(F.col("event_date") == F.lit(date(2026, 4, 1)),
                   F.lit(date(2026, 4, 2))).otherwise(F.col("event_date")),
        )
        self.assertEqual((WarehouseFinding(
            "measure_mismatch", f"{UPTIME_FACT}.event_date", 1
        ),), self.measure_findings(frames))

    def test_direct_uptime_minute_drift_is_reported_by_column(self) -> None:
        frames = dict(self.frames)
        frames[UPTIME_FACT] = self.frames[UPTIME_FACT].withColumn(
            "running_minutes",
            F.when(F.col("event_date") == F.lit(date(2026, 4, 1)),
                   F.col("running_minutes") + 1).otherwise(F.col("running_minutes")),
        )
        self.assertEqual((WarehouseFinding(
            "measure_mismatch", f"{UPTIME_FACT}.running_minutes", 1
        ),), self.measure_findings(frames))

    def test_derived_downtime_impact_ratio_drift_is_reported(self) -> None:
        frames = dict(self.frames)
        frames[UPTIME_FACT] = self.frames[UPTIME_FACT].withColumn(
            "downtime_impact_ratio_pct",
            F.when(F.col("event_date") == F.lit(date(2026, 4, 2)),
                   F.lit(99.99)).otherwise(F.col("downtime_impact_ratio_pct")),
        )
        self.assertEqual((WarehouseFinding(
            "measure_mismatch", f"{UPTIME_FACT}.downtime_impact_ratio_pct", 1
        ),), self.measure_findings(frames))

    def test_impact_ratio_above_one_hundred_reconciles(self) -> None:
        gold = self.gold_uptime.withColumn(
            "downtime_minutes",
            F.when(F.col("event_date") == F.lit(date(2026, 4, 2)), F.lit(90))
            .otherwise(F.col("downtime_minutes")),
        )
        frames = build_warehouse_frames(gold, self.gold_failures)
        ratio = frames[UPTIME_FACT].where(
            F.col("event_date") == F.lit(date(2026, 4, 2))
        ).select("downtime_impact_ratio_pct").collect()[0][0]
        self.assertEqual(200.0, ratio)
        self.assertEqual((), audit_warehouse_measures(
            gold_uptime=gold,
            gold_failures=self.gold_failures,
            warehouse_frames=frames,
        ))

    def test_failure_cost_quantity_count_and_null_drift_are_reported(self) -> None:
        frames = dict(self.frames)
        frames[FAILURE_FACT] = (
            self.frames[FAILURE_FACT]
            .withColumn("maintenance_cost_gbp", F.col("maintenance_cost_gbp") + 1.0)
            .withColumn("part_quantity", F.col("part_quantity") + 1)
            .withColumn("failure_event_count", F.lit(2))
            .withColumn("temperature_c", F.lit(None).cast("double"))
        )
        self.assertEqual({
            WarehouseFinding("measure_mismatch", f"{FAILURE_FACT}.failure_event_count", 1),
            WarehouseFinding("measure_mismatch", f"{FAILURE_FACT}.maintenance_cost_gbp", 1),
            WarehouseFinding("measure_mismatch", f"{FAILURE_FACT}.part_quantity", 1),
            WarehouseFinding("measure_mismatch", f"{FAILURE_FACT}.temperature_c", 1),
        }, set(self.measure_findings(frames)))


if __name__ == "__main__":
    unittest.main()
