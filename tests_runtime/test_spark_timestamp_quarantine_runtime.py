"""Execute timestamp quarantine under both ANSI modes without workspace writes."""

from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from py4j.protocol import Py4JJavaError
from pyspark.errors import PySparkException
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, TimestampType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.spark_medallion import (  # noqa: E402
    CONFLICTING_EVENT_ID_REASON,
    INVALID_REQUIRED_KEY_REASON,
    build_silver_frames,
    raw_machine_event_schema,
    reconcile_silver,
)
from test_spark_medallion_runtime import _event  # noqa: E402


class SparkTimestampQuarantineRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-timestamp-quarantine")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.addClassCleanup(cls.spark.stop)
        cls.spark.sparkContext.setLogLevel("ERROR")
        cls.schema = raw_machine_event_schema()
        cls.schema.add("_ingested_at", TimestampType(), False)
        cls.schema.add("_source_file", StringType(), False)

    @contextmanager
    def configuration(self, ansi: str, timestamp_type: str = "TIMESTAMP_LTZ"):
        settings = {"spark.sql.ansi.enabled": ansi,
                    "spark.sql.timestampType": timestamp_type}
        previous = {key: self.spark.conf.get(key) for key in settings}
        try:
            for key, value in settings.items():
                self.spark.conf.set(key, value)
            yield
            for key, value in settings.items():
                self.assertEqual(value, self.spark.conf.get(key))
        finally:
            for key, value in previous.items():
                self.spark.conf.set(key, value)

    @contextmanager
    def frames(self, rows):
        bronze = self.spark.createDataFrame(rows, schema=self.schema)
        frames = build_silver_frames(bronze)
        silver, quarantine = frames["silver"].cache(), frames["quarantine"].cache()
        try:
            yield bronze, silver, quarantine
        finally:
            quarantine.unpersist(blocking=True)
            silver.unpersist(blocking=True)

    def test_reference_timestamp_cast_reproduces_ansi_failure(self) -> None:
        # This is the exact original conversion, not an alleged full legacy run.
        with self.configuration("true"):
            source = self.spark.createDataFrame([("bad-timestamp",)], "event_ts STRING")
            with self.assertRaises((Py4JJavaError, PySparkException)) as raised:
                source.select(F.to_timestamp("event_ts")).collect()
            self.assertIn("CAST_INVALID_INPUT", str(raised.exception))
            self.assertIsNone(source.select(F.try_to_timestamp("event_ts")).first()[0])

    def test_malformed_timestamps_reach_quarantine_in_both_modes(self) -> None:
        values = ("bad-timestamp", "2026-02-30T08:00:00Z", "", " ", None)
        rows = [_event(event_id=f"BAD{index}", event_ts=value)
                for index, value in enumerate(values)] + [_event(event_id="GOOD")]
        for ansi in ("false", "true"):
            with self.subTest(ansi=ansi), self.configuration(ansi), self.frames(rows) as frames:
                bronze, silver, quarantine = frames
                rejected = {row["event_id"]: row.asDict() for row in quarantine.collect()}
                self.assertEqual({f"BAD{index}" for index in range(len(values))}, set(rejected))
                for index, value in enumerate(values):
                    row = rejected[f"BAD{index}"]
                    self.assertEqual(value, row["event_ts"])
                    self.assertIsNone(row["event_ts_utc"])
                    self.assertIsNone(row["event_date"])
                    self.assertEqual(INVALID_REQUIRED_KEY_REASON, row["quarantine_reason"])
                    self.assertEqual(64, len(row["event_payload_sha256"]))
                self.assertEqual(["GOOD"], [row.event_id for row in silver.collect()])
                result = reconcile_silver(bronze, silver, quarantine)
                self.assertTrue(result.is_reconciled)
                self.assertEqual((6, 1, 5, 0), (result.bronze_rows, result.silver_rows,
                                 result.invalid_quarantine_rows, result.deduplicated_rows))

    def test_valid_values_and_timestamp_types_match_original_parser(self) -> None:
        values = ("2026-04-02T08:00:00Z", "2026-04-02T09:00:00+01:00",
                  "2026-04-02T08:00:00.123456Z")
        rows = [_event(event_id=f"E{index}", event_ts=value)
                for index, value in enumerate(values)]
        for ansi in ("false", "true"):
            for timestamp_type in ("TIMESTAMP_LTZ", "TIMESTAMP_NTZ"):
                with self.subTest(ansi=ansi, timestamp_type=timestamp_type):
                    with self.configuration(ansi, timestamp_type), self.frames(rows) as frames:
                        bronze, silver, quarantine = frames
                        original = bronze.select("event_id", F.to_timestamp("event_ts").alias("event_ts_utc"))
                        actual = silver.select("event_id", "event_ts_utc")
                        self.assertEqual(original.schema, actual.schema)
                        self.assertEqual(original.orderBy("event_id").collect(),
                                         actual.orderBy("event_id").collect())
                        self.assertEqual(0, quarantine.count())

    def test_bad_timestamp_conflict_preserves_all_evidence_without_a_winner(self) -> None:
        rows = [_event(event_id="CONFLICT"),
                _event(event_id="CONFLICT", event_ts="bad-timestamp"),
                _event(event_id="GOOD")]
        for ansi in ("false", "true"):
            with self.subTest(ansi=ansi), self.configuration(ansi), self.frames(rows) as frames:
                bronze, silver, quarantine = frames
                result = reconcile_silver(bronze, silver, quarantine)
                self.assertTrue(result.is_reconciled)
                self.assertEqual((1, 1, 1), (result.invalid_quarantine_rows,
                                 result.conflicting_quarantine_rows, result.conflicting_event_ids))
                self.assertEqual(["GOOD"], [row.event_id for row in silver.collect()])
                rejected = quarantine.collect()
                self.assertTrue(all(row.is_conflicting_event_id for row in rejected))
                self.assertEqual({INVALID_REQUIRED_KEY_REASON, CONFLICTING_EVENT_ID_REASON},
                                 {row.quarantine_reason for row in rejected})
                self.assertEqual(2, len({row.event_payload_sha256 for row in rejected}))

    def test_valid_replay_retains_latest_delivery_in_both_modes(self) -> None:
        rows = [_event(), _event(_ingested_at=datetime(2026, 4, 10),
                                 _source_file="/landing/replay.csv")]
        for ansi in ("false", "true"):
            with self.subTest(ansi=ansi), self.configuration(ansi), self.frames(rows) as frames:
                bronze, silver, quarantine = frames
                result = reconcile_silver(bronze, silver, quarantine)
                self.assertTrue(result.is_reconciled)
                self.assertEqual((2, 1, 1, 0), (result.bronze_rows, result.silver_rows,
                                 result.deduplicated_rows, result.quarantine_rows))
                self.assertEqual("/landing/replay.csv", silver.first()._source_file)


if __name__ == "__main__":
    unittest.main()
