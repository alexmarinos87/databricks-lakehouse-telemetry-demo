from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from functools import reduce
from operator import or_
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_warehouse import (  # noqa: E402
    FAILURE_FACT,
    UPTIME_FACT,
    WarehouseFinding,
    audit_warehouse,
    build_warehouse_frames,
)


UPTIME_SCHEMA = StructType(
    [
        StructField("event_date", DateType(), False),
        StructField("site_id", StringType(), False),
        StructField("client_id", StringType(), False),
        StructField("machine_id", StringType(), False),
        StructField("model", StringType(), False),
        StructField("running_minutes", IntegerType(), False),
        StructField("idle_minutes", IntegerType(), False),
        StructField("maintenance_minutes", IntegerType(), False),
        StructField("downtime_minutes", IntegerType(), False),
        StructField("observed_minutes", IntegerType(), False),
        StructField("uptime_pct", DoubleType(), True),
        StructField("avg_health_score", DoubleType(), True),
    ]
)

FAILURE_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), False),
        StructField("event_date", DateType(), False),
        StructField("event_ts_utc", TimestampType(), False),
        StructField("site_id", StringType(), False),
        StructField("client_id", StringType(), False),
        StructField("machine_id", StringType(), False),
        StructField("model", StringType(), False),
        StructField("fault_code", StringType(), False),
        StructField("severity", StringType(), False),
        StructField("temperature_c", DoubleType(), True),
        StructField("vibration_mm_s", DoubleType(), True),
        StructField("downtime_minutes", IntegerType(), True),
        StructField("maintenance_cost_gbp", DoubleType(), False),
        StructField("part_code", StringType(), True),
        StructField("part_quantity", IntegerType(), False),
    ]
)


def _uptime_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_date": date(2026, 4, 1),
        "site_id": "S1",
        "client_id": "C1",
        "machine_id": "M1",
        "model": "EXC-100",
        "running_minutes": 60,
        "idle_minutes": 0,
        "maintenance_minutes": 0,
        "downtime_minutes": 0,
        "observed_minutes": 60,
        "uptime_pct": 100.0,
        "avg_health_score": 95.0,
    }
    row.update(overrides)
    return row


def _failure_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
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
    row.update(overrides)
    return row


class SparkWarehouseRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-warehouse-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

        cls.uptime = cls.spark.createDataFrame(
            [
                _uptime_row(),
                _uptime_row(
                    event_date=date(2026, 4, 2),
                    site_id="S2",
                    machine_id="M2",
                    model="EXC-200",
                    running_minutes=30,
                    idle_minutes=15,
                    downtime_minutes=15,
                    observed_minutes=45,
                    uptime_pct=66.67,
                    avg_health_score=70.0,
                ),
            ],
            schema=UPTIME_SCHEMA,
        ).cache()
        cls.failures = cls.spark.createDataFrame(
            [_failure_row()], schema=FAILURE_SCHEMA
        ).cache()
        cls.warehouse = {
            name: dataframe.cache()
            for name, dataframe in build_warehouse_frames(
                cls.uptime, cls.failures
            ).items()
        }

        cls.uptime.count()
        cls.failures.count()
        for dataframe in cls.warehouse.values():
            dataframe.count()

    @classmethod
    def tearDownClass(cls) -> None:
        for dataframe in [cls.uptime, cls.failures, *cls.warehouse.values()]:
            dataframe.unpersist()
        cls.spark.stop()

    def test_valid_warehouse_reconciles_and_has_expected_counts(self) -> None:
        findings = audit_warehouse(
            gold_uptime=self.uptime,
            gold_failures=self.failures,
            warehouse_frames=self.warehouse,
        )

        self.assertEqual((), findings)
        self.assertEqual(2, self.warehouse[UPTIME_FACT].count())
        self.assertEqual(1, self.warehouse[FAILURE_FACT].count())
        self.assertEqual(2, self.warehouse["dim_machine"].count())
        self.assertEqual(2, self.warehouse["dim_date"].count())
        self.assertEqual(1, self.warehouse["dim_fault"].count())

    def test_fact_grains_and_dimension_keys_are_non_null_and_unique(self) -> None:
        uptime_fact = self.warehouse[UPTIME_FACT]
        failure_fact = self.warehouse[FAILURE_FACT]

        self.assertEqual(
            uptime_fact.count(),
            uptime_fact.select("date_key", "machine_key").distinct().count(),
        )
        self.assertEqual(
            failure_fact.count(),
            failure_fact.select("event_id").distinct().count(),
        )
        key_columns = [
            "date_key",
            "client_key",
            "machine_key",
            "model_key",
            "site_key",
        ]
        self.assertEqual(
            0,
            uptime_fact.where(
                reduce(or_, (F.col(column).isNull() for column in key_columns))
            ).count(),
        )

    def test_audit_detects_unmatched_fault_member(self) -> None:
        corrupted = dict(self.warehouse)
        corrupted["dim_fault"] = self.spark.createDataFrame(
            [], schema=self.warehouse["dim_fault"].schema
        )

        findings = audit_warehouse(
            gold_uptime=self.uptime,
            gold_failures=self.failures,
            warehouse_frames=corrupted,
        )

        self.assertIn(
            WarehouseFinding(
                code="unmatched_dimension_key",
                dataset=f"{FAILURE_FACT}.fault_key",
                count=1,
            ),
            findings,
        )

    def test_audit_detects_duplicate_uptime_fact_and_count_gain(self) -> None:
        corrupted = dict(self.warehouse)
        corrupted[UPTIME_FACT] = self.warehouse[UPTIME_FACT].unionByName(
            self.warehouse[UPTIME_FACT].limit(1)
        )

        findings = audit_warehouse(
            gold_uptime=self.uptime,
            gold_failures=self.failures,
            warehouse_frames=corrupted,
        )

        self.assertIn(
            WarehouseFinding(
                code="source_fact_count_mismatch",
                dataset=UPTIME_FACT,
                count=1,
            ),
            findings,
        )
        self.assertIn(
            WarehouseFinding(
                code="duplicate_fact_grain",
                dataset=UPTIME_FACT,
                count=1,
            ),
            findings,
        )

    def test_conflicting_machine_assignment_fails_closed(self) -> None:
        conflict = self.spark.createDataFrame(
            [
                _uptime_row(
                    event_date=date(2026, 4, 3),
                    site_id="S9",
                    client_id="C9",
                    machine_id="M1",
                    model="OTHER",
                )
            ],
            schema=UPTIME_SCHEMA,
        )

        with self.assertRaisesRegex(ValueError, "conflicting machine assignments"):
            build_warehouse_frames(
                self.uptime.unionByName(conflict), self.failures
            )

    def test_failure_only_machine_is_represented(self) -> None:
        extra_failure = self.spark.createDataFrame(
            [
                _failure_row(
                    event_id="E3",
                    event_date=date(2026, 4, 3),
                    event_ts_utc=datetime(2026, 4, 3, 11, 0, 0),
                    site_id="S3",
                    client_id="C2",
                    machine_id="M3",
                    model="LDR-300",
                    fault_code="ELEC-01",
                    severity="medium",
                )
            ],
            schema=FAILURE_SCHEMA,
        )
        failures = self.failures.unionByName(extra_failure)
        warehouse = build_warehouse_frames(self.uptime, failures)

        self.assertEqual(3, warehouse["dim_machine"].count())
        self.assertEqual(2, warehouse[FAILURE_FACT].count())
        self.assertEqual(
            (),
            audit_warehouse(
                gold_uptime=self.uptime,
                gold_failures=failures,
                warehouse_frames=warehouse,
            ),
        )

    def test_audit_fails_closed_for_missing_dataset(self) -> None:
        incomplete = dict(self.warehouse)
        incomplete.pop("dim_date")

        with self.assertRaisesRegex(ValueError, "dim_date"):
            audit_warehouse(
                gold_uptime=self.uptime,
                gold_failures=self.failures,
                warehouse_frames=incomplete,
            )

    def test_empty_uptime_fails_closed(self) -> None:
        empty_uptime = self.spark.createDataFrame([], schema=UPTIME_SCHEMA)

        with self.assertRaisesRegex(ValueError, "gold uptime must contain"):
            build_warehouse_frames(empty_uptime, self.failures)


if __name__ == "__main__":
    unittest.main()
