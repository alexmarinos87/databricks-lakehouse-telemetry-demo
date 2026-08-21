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
from lakehouse_demo.warehouse_identity import (  # noqa: E402
    audit_warehouse_publication,
)


class WarehouseIdentityRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-warehouse-identity-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

        cls.gold_uptime = cls.spark.createDataFrame(
            [
                {
                    "event_date": date(2026, 4, 1),
                    "site_id": "S1",
                    "client_id": "C1",
                    "machine_id": "M1",
                    "model": "EXC-100",
                    "running_minutes": 50,
                    "idle_minutes": 10,
                    "maintenance_minutes": 0,
                    "downtime_minutes": 0,
                    "observed_minutes": 60,
                    "uptime_pct": 83.33,
                    "avg_health_score": 95.0,
                },
                {
                    "event_date": date(2026, 4, 2),
                    "site_id": "S2",
                    "client_id": "C1",
                    "machine_id": "M2",
                    "model": "EXC-200",
                    "running_minutes": 30,
                    "idle_minutes": 0,
                    "maintenance_minutes": 15,
                    "downtime_minutes": 15,
                    "observed_minutes": 45,
                    "uptime_pct": 66.67,
                    "avg_health_score": 55.0,
                },
            ]
        ).cache()
        cls.gold_failures = cls.spark.createDataFrame(
            [
                {
                    "event_id": "E2",
                    "event_date": date(2026, 4, 2),
                    "event_ts_utc": datetime(2026, 4, 2, 9, 30, 0),
                    "site_id": "S2",
                    "client_id": "C1",
                    "machine_id": "M2",
                    "model": "EXC-200",
                    "fault_code": "HYD-01",
                    "severity": "high",
                    "temperature_c": 95.0,
                    "vibration_mm_s": 7.5,
                    "downtime_minutes": 15,
                    "maintenance_cost_gbp": 250.0,
                    "part_code": "PUMP",
                    "part_quantity": 1,
                }
            ]
        ).cache()
        cls.frames = {
            name: dataframe.cache()
            for name, dataframe in build_warehouse_frames(
                cls.gold_uptime,
                cls.gold_failures,
            ).items()
        }

        cls.gold_uptime.count()
        cls.gold_failures.count()
        for dataframe in cls.frames.values():
            dataframe.count()

    @classmethod
    def tearDownClass(cls) -> None:
        for dataframe in [
            cls.gold_uptime,
            cls.gold_failures,
            *cls.frames.values(),
        ]:
            dataframe.unpersist()
        cls.spark.stop()

    def test_clean_warehouse_passes_composite_publication_audit(self) -> None:
        self.assertEqual(
            (),
            audit_warehouse_publication(
                gold_uptime=self.gold_uptime,
                gold_failures=self.gold_failures,
                warehouse_frames=self.frames,
            ),
        )

    def test_equal_counts_and_valid_foreign_keys_do_not_hide_wrong_uptime_identity(
        self,
    ) -> None:
        machine_keys = {
            row["machine_id"]: row["machine_key"]
            for row in self.frames["dim_machine"]
            .select("machine_id", "machine_key")
            .collect()
        }
        corrupted_uptime = self.frames[UPTIME_FACT].withColumn(
            "machine_key",
            F.when(
                F.col("event_date") == F.lit(date(2026, 4, 1)),
                F.lit(machine_keys["M2"]),
            ).otherwise(F.col("machine_key")),
        )
        corrupted_frames = dict(self.frames)
        corrupted_frames[UPTIME_FACT] = corrupted_uptime

        findings = audit_warehouse_publication(
            gold_uptime=self.gold_uptime,
            gold_failures=self.gold_failures,
            warehouse_frames=corrupted_frames,
        )

        self.assertEqual(
            {
                WarehouseFinding(
                    code="missing_fact_identity",
                    dataset=UPTIME_FACT,
                    count=1,
                ),
                WarehouseFinding(
                    code="unexpected_fact_identity",
                    dataset=UPTIME_FACT,
                    count=1,
                ),
            },
            set(findings),
        )

    def test_equal_counts_do_not_hide_wrong_failure_event_identity(self) -> None:
        corrupted_failure = self.frames[FAILURE_FACT].withColumn(
            "event_id",
            F.lit("E_UNEXPECTED"),
        )
        corrupted_frames = dict(self.frames)
        corrupted_frames[FAILURE_FACT] = corrupted_failure

        findings = audit_warehouse_publication(
            gold_uptime=self.gold_uptime,
            gold_failures=self.gold_failures,
            warehouse_frames=corrupted_frames,
        )

        self.assertEqual(
            {
                WarehouseFinding(
                    code="missing_fact_identity",
                    dataset=FAILURE_FACT,
                    count=1,
                ),
                WarehouseFinding(
                    code="unexpected_fact_identity",
                    dataset=FAILURE_FACT,
                    count=1,
                ),
            },
            set(findings),
        )


if __name__ == "__main__":
    unittest.main()
