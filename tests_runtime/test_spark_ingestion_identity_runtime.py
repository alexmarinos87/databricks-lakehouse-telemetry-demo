from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from pyspark.sql import SparkSession


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_ingestion_identity import (  # noqa: E402
    with_ingestion_identity,
)


class SparkIngestionIdentityRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-ingestion-identity-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def test_incremental_and_backfill_names_produce_governed_lineage(self) -> None:
        digest_a = "a" * 64
        digest_b = "b" * 64
        source = self.spark.createDataFrame(
            [
                {
                    "event_id": "E1",
                    "_source_file": (
                        "dbfs:/Volumes/main/demo/files/raw_machine_events/"
                        f"machine-events__incremental__sha256_{digest_a}.csv"
                    ),
                },
                {
                    "event_id": "E2",
                    "_source_file": (
                        "dbfs:/Volumes/main/demo/files/raw_machine_events/"
                        "machine-events__backfill__replay_repair-2026-08-22__"
                        f"sha256_{digest_b}.csv"
                    ),
                },
            ]
        )

        rows = {
            row["event_id"]: row
            for row in with_ingestion_identity(source).collect()
        }

        self.assertTrue(rows["E1"]["_source_identity_valid"])
        self.assertEqual("incremental", rows["E1"]["_ingestion_mode"])
        self.assertIsNone(rows["E1"]["_replay_id"])
        self.assertEqual(digest_a, rows["E1"]["_source_content_sha256"])
        self.assertTrue(rows["E2"]["_source_identity_valid"])
        self.assertEqual("backfill", rows["E2"]["_ingestion_mode"])
        self.assertEqual("repair-2026-08-22", rows["E2"]["_replay_id"])
        self.assertEqual(digest_b, rows["E2"]["_source_content_sha256"])

    def test_legacy_fixed_name_is_explicitly_invalid(self) -> None:
        source = self.spark.createDataFrame(
            [
                {
                    "event_id": "E1",
                    "_source_file": (
                        "dbfs:/Volumes/main/demo/files/raw_machine_events/"
                        "sample_machine_events.csv"
                    ),
                }
            ]
        )

        row = with_ingestion_identity(source).collect()[0]

        self.assertFalse(row["_source_identity_valid"])
        self.assertEqual("", row["_ingestion_mode"])
        self.assertEqual("", row["_source_content_sha256"])


if __name__ == "__main__":
    unittest.main()
