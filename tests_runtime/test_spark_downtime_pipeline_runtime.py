from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
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

from lakehouse_demo.downtime_pipeline import (  # noqa: E402
    GOLD_UPTIME,
    build_governed_gold_frames,
    build_governed_warehouse_frames,
    materialized_downtime_findings,
)
from lakehouse_demo.spark_quality import evaluate_quality_tables  # noqa: E402
from lakehouse_demo.spark_warehouse import (  # noqa: E402
    UPTIME_FACT,
    WarehouseFinding,
)
from lakehouse_demo.warehouse_publication import (  # noqa: E402
    audit_warehouse_publication,
)


SILVER_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), False),
        StructField("event_date", DateType(), False),
        StructField("event_ts_utc", TimestampType(), False),
        StructField("site_id", StringType(), False),
        StructField("client_id", StringType(), False),
        StructField("machine_id", StringType(), False),
        StructField("model", StringType(), False),
        StructField("status", StringType(), False),
        StructField("fault_code", StringType(), False),
        StructField("severity", StringType(), False),
        StructField("temperature_c", DoubleType(), True),
        StructField("vibration_mm_s", DoubleType(), True),
        StructField("duration_minutes", IntegerType(), False),
        StructField("downtime_minutes", IntegerType(), False),
        StructField("maintenance_cost_gbp", DoubleType(), False),
        StructField("part_code", StringType(), False),
        StructField("part_quantity", IntegerType(), False),
        StructField("health_score", IntegerType(), False),
        StructField("is_failure_event", BooleanType(), False),
    ]
)


def _silver_event(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "E1",
        "event_date": date(2026, 4, 1),
        "event_ts_utc": datetime(2026, 4, 1, 8, 0, 0),
        "site_id": "S1",
        "client_id": "C1",
        "machine_id": "M1",
        "model": "EXC-100",
        "status": "RUNNING",
        "fault_code": "OK",
        "severity": "none",
        "temperature_c": 70.0,
        "vibration_mm_s": 2.0,
        "duration_minutes": 60,
        "downtime_minutes": 120,
        "maintenance_cost_gbp": 0.0,
        "part_code": "NONE",
        "part_quantity": 0,
        "health_score": 100,
        "is_failure_event": False,
    }
    row.update(overrides)
    return row


class SparkDowntimePipelineRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-downtime-pipeline-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def build_outputs(self, **overrides: object):
        silver = self.spark.createDataFrame(
            [_silver_event(**overrides)], schema=SILVER_SCHEMA
        )
        gold = build_governed_gold_frames(silver)
        warehouse = build_governed_warehouse_frames(
            gold[GOLD_UPTIME], gold["gold_failure_events"]
        )
        return gold, warehouse

    def test_load_above_100_is_materialized_and_publishable(self) -> None:
        gold, warehouse = self.build_outputs()
        gold_row = gold[GOLD_UPTIME].collect()[0]
        fact_row = warehouse[UPTIME_FACT].collect()[0]

        for row in (gold_row, fact_row):
            self.assertEqual(120, row["downtime_minutes"])
            self.assertEqual(60, row["observed_minutes"])
            self.assertEqual(200.0, row["downtime_pct"])
            self.assertEqual(200.0, row["downtime_load_pct"])
            self.assertTrue(row["downtime_exceeds_observed"])
            self.assertEqual(
                "attributed_incident_v1", row["downtime_semantics_version"]
            )

        self.assertEqual((), materialized_downtime_findings(gold[GOLD_UPTIME]))
        self.assertEqual(
            (),
            audit_warehouse_publication(
                gold_uptime=gold[GOLD_UPTIME],
                gold_failures=gold["gold_failure_events"],
                warehouse_frames=warehouse,
            ),
        )

    def test_zero_observation_with_attributed_downtime_has_no_load_denominator(self) -> None:
        gold, warehouse = self.build_outputs(
            duration_minutes=0,
            downtime_minutes=30,
        )

        for dataframe in (gold[GOLD_UPTIME], warehouse[UPTIME_FACT]):
            row = dataframe.collect()[0]
            self.assertEqual(0, row["observed_minutes"])
            self.assertEqual(30, row["downtime_minutes"])
            self.assertIsNone(row["downtime_pct"])
            self.assertIsNone(row["downtime_load_pct"])
            self.assertTrue(row["downtime_exceeds_observed"])
            self.assertEqual((), materialized_downtime_findings(dataframe))

        self.assertEqual(
            (),
            audit_warehouse_publication(
                gold_uptime=gold[GOLD_UPTIME],
                gold_failures=gold["gold_failure_events"],
                warehouse_frames=warehouse,
            ),
        )

    def test_quality_accepts_high_but_reconciled_downtime_load(self) -> None:
        _, warehouse = self.build_outputs()
        results = evaluate_quality_tables(
            {UPTIME_FACT: warehouse[UPTIME_FACT]},
            expected_tables=(UPTIME_FACT,),
        )
        semantic = next(
            result
            for result in results
            if result.check_name == "uptime_fact_downtime_semantics_valid"
        )

        self.assertEqual("pass", semantic.status)
        self.assertEqual("error", semantic.severity)
        self.assertEqual(0, semantic.observed_count)
        self.assertFalse(any(result.status == "fail" for result in results))

    def test_corrupted_load_blocks_quality_and_warehouse_publication(self) -> None:
        gold, warehouse = self.build_outputs()
        corrupted = dict(warehouse)
        corrupted[UPTIME_FACT] = warehouse[UPTIME_FACT].withColumn(
            "downtime_load_pct", F.lit(999.0)
        )

        quality_results = evaluate_quality_tables(
            {UPTIME_FACT: corrupted[UPTIME_FACT]},
            expected_tables=(UPTIME_FACT,),
        )
        semantic_quality = next(
            result
            for result in quality_results
            if result.check_name == "uptime_fact_downtime_semantics_valid"
        )
        findings = audit_warehouse_publication(
            gold_uptime=gold[GOLD_UPTIME],
            gold_failures=gold["gold_failure_events"],
            warehouse_frames=corrupted,
        )

        self.assertEqual("fail", semantic_quality.status)
        self.assertEqual("error", semantic_quality.severity)
        self.assertIn(
            WarehouseFinding(
                code="downtime_load_formula_mismatch",
                dataset=UPTIME_FACT,
                count=1,
            ),
            findings,
        )
        self.assertIn(
            WarehouseFinding(
                code="measure_mismatch",
                dataset=f"{UPTIME_FACT}.downtime_load_pct",
                count=1,
            ),
            findings,
        )

    def test_partial_semantic_schema_fails_closed(self) -> None:
        gold, _ = self.build_outputs()
        partial = gold[GOLD_UPTIME].drop("downtime_semantics_version")

        with self.assertRaisesRegex(ValueError, "partial downtime semantic schema"):
            build_governed_warehouse_frames(
                partial,
                gold["gold_failure_events"],
            )


if __name__ == "__main__":
    unittest.main()
