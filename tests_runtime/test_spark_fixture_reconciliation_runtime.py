"""Compare committed source evidence with real Spark, without workspace writes."""

from __future__ import annotations

import csv
import os
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, TimestampType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.azure_ingestion import MACHINE_EVENT_COLUMNS  # noqa: E402
from lakehouse_demo.dataset_profile import (  # noqa: E402
    default_machine_event_sources,
    profile_machine_event_files,
)
from lakehouse_demo.downtime_pipeline import build_governed_gold_frames  # noqa: E402
from lakehouse_demo.spark_medallion import (  # noqa: E402
    build_silver_frames,
    raw_machine_event_schema,
    reconcile_silver,
)


class SparkFixtureReconciliationRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = default_machine_event_sources(ROOT)
        cls.profile = profile_machine_event_files(ROOT, cls.sources)
        records = []
        for relative in cls.sources:
            with (ROOT / relative).open(encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream):
                    records.append({
                        **row,
                        "_source_file": f"fixture://{relative}",
                        "_ingested_at": datetime(2026, 4, 4) + timedelta(seconds=len(records)),
                    })

        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-committed-fixture-reconciliation")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.addClassCleanup(cls.spark.stop)
        cls.spark.sparkContext.setLogLevel("ERROR")
        schema = raw_machine_event_schema()
        schema.add("_source_file", StringType(), False)
        schema.add("_ingested_at", TimestampType(), False)
        cls.bronze = cls.spark.createDataFrame(records, schema=schema).cache()
        cls.addClassCleanup(cls.bronze.unpersist)
        frames = build_silver_frames(cls.bronze)
        cls.silver = frames["silver"].cache()
        cls.quarantine = frames["quarantine"].cache()
        cls.addClassCleanup(cls.silver.unpersist)
        cls.addClassCleanup(cls.quarantine.unpersist)
        cls.gold = build_governed_gold_frames(cls.silver)

    @staticmethod
    def totals(frame, *columns):
        row = frame.agg(*[F.sum(name).alias(name) for name in columns]).first()
        return row.asDict()

    def test_committed_schema_rows_and_replays_reconcile(self) -> None:
        self.assertEqual(list(MACHINE_EVENT_COLUMNS), raw_machine_event_schema().fieldNames())
        expected = self.profile["rows"]
        # Lock the demonstration fixture, not just agreement between two implementations.
        self.assertEqual(2, len(self.sources))
        self.assertEqual((31, 30, 1), (
            expected["physical_row_count"], expected["unique_event_id_count"],
            expected["replay_duplicate_row_count"],
        ))
        observed = reconcile_silver(self.bronze, self.silver, self.quarantine)
        self.assertTrue(observed.is_reconciled)
        self.assertFalse(observed.has_conflicts)
        self.assertEqual(expected["physical_row_count"], observed.bronze_rows)
        self.assertEqual(expected["unique_event_id_count"], observed.silver_rows)
        self.assertEqual(expected["replay_duplicate_row_count"], observed.deduplicated_rows)
        self.assertEqual(0, observed.quarantine_rows)
        self.assertEqual(observed.silver_rows, self.silver.select("event_id").distinct().count())

    def test_committed_coverage_and_categories_match_source_profile(self) -> None:
        coverage = self.profile["coverage"]
        for column, key in (
            ("machine_id", "machine_count"), ("client_id", "client_count"),
            ("site_id", "site_count"), ("model", "model_count"),
        ):
            with self.subTest(column=column):
                self.assertEqual(coverage[key], self.silver.select(column).distinct().count())
        observed = self.silver.agg(F.min("event_ts"), F.max("event_ts")).first()
        self.assertEqual((coverage["observation_start"], coverage["observation_end"]), tuple(observed))
        for column, key in (("status", "status_counts"), ("event_type", "event_type_counts")):
            counts = {row[column]: row["count"] for row in self.silver.groupBy(column).count().collect()}
            self.assertEqual(self.profile["operations"][key], counts)

    def test_silver_and_governed_gold_preserve_fixture_operational_totals(self) -> None:
        expected = self.profile["operations"]
        silver = self.totals(self.silver, "duration_minutes", "downtime_minutes", "part_quantity", "maintenance_cost_gbp")
        for column, key in (
            ("duration_minutes", "duration_minutes_total"),
            ("downtime_minutes", "downtime_minutes_total"),
            ("part_quantity", "part_quantity_total"),
        ):
            self.assertEqual(expected[key], silver[column])
        # These small fixture amounts are exactly representable. This is NOT a
        # general equivalence claim between arbitrary Decimal and Spark double sums.
        cost = Decimal(expected["maintenance_cost_gbp_total"])
        self.assertEqual(cost, Decimal(str(silver["maintenance_cost_gbp"])))
        uptime = self.totals(self.gold["gold_machine_uptime"], "observed_minutes", "downtime_minutes")
        self.assertEqual(expected["duration_minutes_total"], uptime["observed_minutes"])
        self.assertEqual(expected["downtime_minutes_total"], uptime["downtime_minutes"])
        maintenance = self.totals(self.gold["gold_maintenance_costs"], "maintenance_cost_gbp", "downtime_minutes")
        self.assertEqual(cost, Decimal(str(maintenance["maintenance_cost_gbp"])))
        self.assertEqual(expected["downtime_minutes_total"], maintenance["downtime_minutes"])

    def test_whole_fixture_replay_preserves_business_rows_and_gold_totals(self) -> None:
        later = (
            self.bronze.withColumn("_ingested_at", F.lit(datetime(2026, 4, 10)))
            .withColumn("_source_file", F.concat(F.lit("replay://"), F.col("_source_file")))
        )
        bronze = self.bronze.unionByName(later).repartition(3).cache()
        self.addCleanup(bronze.unpersist)
        frames = build_silver_frames(bronze)
        silver = frames["silver"].cache()
        quarantine = frames["quarantine"].cache()
        self.addCleanup(silver.unpersist)
        self.addCleanup(quarantine.unpersist)
        observed = reconcile_silver(bronze, silver, quarantine)
        expected = self.profile["rows"]
        self.assertTrue(observed.is_reconciled)
        self.assertFalse(observed.has_conflicts)
        self.assertEqual(2 * expected["physical_row_count"], observed.bronze_rows)
        self.assertEqual(expected["unique_event_id_count"], observed.silver_rows)
        self.assertEqual(0, observed.quarantine_rows)
        self.assertEqual(observed.bronze_rows - expected["unique_event_id_count"], observed.deduplicated_rows)
        business_columns = sorted(set(self.silver.columns) - {"_source_file", "_ingested_at"})
        self.assertEqual(
            self.silver.select(*business_columns).orderBy("event_id").collect(),
            silver.select(*business_columns).orderBy("event_id").collect(),
        )
        self.assertEqual(0, silver.where(~F.col("_source_file").startswith("replay://")).count())
        gold = build_governed_gold_frames(silver)
        for name, columns in (
            ("gold_machine_uptime", ("observed_minutes", "downtime_minutes")),
            ("gold_maintenance_costs", ("maintenance_cost_gbp", "downtime_minutes")),
        ):
            with self.subTest(output=name):
                self.assertEqual(self.totals(self.gold[name], *columns), self.totals(gold[name], *columns))


if __name__ == "__main__":
    unittest.main()
