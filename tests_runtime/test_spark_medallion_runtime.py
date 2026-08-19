from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql.types import StringType, TimestampType


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_medallion import (  # noqa: E402
    RAW_MACHINE_EVENT_COLUMNS,
    build_gold_frames,
    build_silver_frames,
    raw_machine_event_schema,
    reconcile_silver,
)


def _event(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "E1",
        "machine_id": "M1",
        "event_ts": "2026-04-02T08:00:00Z",
        "site_id": "S1",
        "client_id": "C1",
        "model": "EXC-100",
        "hour_meter": "120.5",
        "event_type": " telemetry ",
        "status": " running ",
        "fault_code": " ok ",
        "severity": " none ",
        "temperature_c": "72.0",
        "vibration_mm_s": "2.2",
        "fuel_level_pct": "60.0",
        "duration_minutes": "60",
        "downtime_minutes": "0",
        "maintenance_cost_gbp": "0.0",
        "part_code": " none ",
        "part_quantity": "0",
        "operator_shift": "DAY",
        "_ingested_at": datetime(2026, 4, 2, 10, 0, 0),
        "_source_file": "/landing/001_initial.csv",
    }
    row.update(overrides)
    return row


class SparkMedallionRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-medallion-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

        schema = raw_machine_event_schema()
        schema.add("_ingested_at", TimestampType(), False)
        schema.add("_source_file", StringType(), False)

        rows = [
            _event(),
            _event(
                _ingested_at=datetime(2026, 4, 4, 12, 0, 0),
                _source_file="/landing/004_replay.csv",
            ),
            _event(
                event_id="E2",
                machine_id="M2",
                event_ts="2026-04-03T09:30:00Z",
                site_id="S2",
                client_id="C1",
                model="EXC-200",
                hour_meter="510.0",
                event_type=" fault ",
                status=" fault ",
                fault_code=" hyd-01 ",
                severity=" high ",
                temperature_c="95.0",
                vibration_mm_s="7.5",
                fuel_level_pct="15.0",
                duration_minutes="45",
                downtime_minutes="20",
                maintenance_cost_gbp="250.0",
                part_code=" pump ",
                part_quantity="1",
                _ingested_at=datetime(2026, 4, 3, 10, 0, 0),
                _source_file="/landing/002_fault.csv",
            ),
            _event(
                event_id="E3",
                machine_id="M3",
                event_ts="2026-04-01T07:00:00Z",
                site_id="S3",
                client_id="C2",
                model="LDR-300",
                status=" idle ",
                duration_minutes="30",
                downtime_minutes="5",
                _ingested_at=datetime(2026, 4, 5, 8, 0, 0),
                _source_file="/landing/005_late.csv",
            ),
            _event(
                event_id="E_BAD",
                machine_id=" ",
                event_ts="not-a-timestamp",
                _ingested_at=datetime(2026, 4, 3, 11, 0, 0),
                _source_file="/landing/003_invalid.csv",
            ),
        ]

        cls.bronze = cls.spark.createDataFrame(rows, schema=schema).cache()
        silver_frames = build_silver_frames(cls.bronze)
        cls.silver = silver_frames["silver"].cache()
        cls.quarantine = silver_frames["quarantine"].cache()
        cls.gold = {
            name: dataframe.cache()
            for name, dataframe in build_gold_frames(cls.silver).items()
        }

        cls.bronze.count()
        cls.silver.count()
        cls.quarantine.count()
        for dataframe in cls.gold.values():
            dataframe.count()

    @classmethod
    def tearDownClass(cls) -> None:
        for dataframe in [
            cls.bronze,
            cls.silver,
            cls.quarantine,
            *cls.gold.values(),
        ]:
            dataframe.unpersist()
        cls.spark.stop()

    def test_raw_schema_is_source_shaped_and_ordered(self) -> None:
        schema = raw_machine_event_schema()

        self.assertEqual(list(RAW_MACHINE_EVENT_COLUMNS), schema.fieldNames())
        self.assertEqual(
            {"string"},
            {field.dataType.simpleString() for field in schema.fields},
        )

    def test_silver_reconciles_quarantine_and_replay_rows(self) -> None:
        result = reconcile_silver(self.bronze, self.silver, self.quarantine)

        self.assertEqual(5, result.bronze_rows)
        self.assertEqual(4, result.valid_rows_before_deduplication)
        self.assertEqual(1, result.quarantine_rows)
        self.assertEqual(3, result.silver_rows)
        self.assertEqual(1, result.deduplicated_rows)
        self.assertTrue(result.is_reconciled)

    def test_latest_replay_wins_and_values_are_typed_and_normalized(self) -> None:
        replay = (
            self.silver.where("event_id = 'E1'")
            .select("_source_file", "status", "event_type", "duration_minutes")
            .collect()[0]
        )
        data_types = dict(self.silver.dtypes)

        self.assertEqual("/landing/004_replay.csv", replay["_source_file"])
        self.assertEqual("RUNNING", replay["status"])
        self.assertEqual("telemetry", replay["event_type"])
        self.assertEqual(60, replay["duration_minutes"])
        self.assertEqual("int", data_types["duration_minutes"])
        self.assertEqual("double", data_types["temperature_c"])
        self.assertEqual("timestamp", data_types["event_ts_utc"])

    def test_invalid_required_keys_are_quarantined(self) -> None:
        row = self.quarantine.select(
            "event_id", "quarantine_reason"
        ).collect()[0]

        self.assertEqual("E_BAD", row["event_id"])
        self.assertEqual(
            "Missing or invalid required business key",
            row["quarantine_reason"],
        )

    def test_late_event_is_preserved_and_failure_output_is_exact(self) -> None:
        uptime_dates = {
            row["event_date"]
            for row in self.gold["gold_machine_uptime"]
            .select("event_date")
            .collect()
        }
        failure_ids = {
            row["event_id"]
            for row in self.gold["gold_failure_events"]
            .select("event_id")
            .collect()
        }

        self.assertIn(date(2026, 4, 1), uptime_dates)
        self.assertEqual({"E2"}, failure_ids)
        self.assertEqual(3, self.gold["gold_machine_uptime"].count())
        self.assertEqual(1, self.gold["gold_parts_usage"].count())
        self.assertEqual(3, self.gold["gold_client_asset_summary"].count())

    def test_missing_bronze_lineage_column_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "_source_file"):
            build_silver_frames(self.bronze.drop("_source_file"))


if __name__ == "__main__":
    unittest.main()
