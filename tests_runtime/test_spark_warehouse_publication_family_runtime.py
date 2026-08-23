from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_family_publication import (  # noqa: E402
    STATE_COMMITTED,
    audit_family_publication,
    build_family_manifest,
    select_latest_committed_frames,
    transition_family_manifest,
    with_publication_run_id,
)


WAREHOUSE_DATASETS = (
    "dim_client",
    "dim_date",
    "dim_fault",
    "dim_machine",
    "dim_model",
    "dim_site",
    "fact_machine_failure_event",
    "fact_machine_uptime_daily",
)


class SparkWarehousePublicationFamilyRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-warehouse-family-publication-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")
        cls.run_id_column = "warehouse_publication_run_id"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def frames(self, run_id: str, seed: int):
        raw = {
            "dim_client": self.spark.createDataFrame(
                [{"client_key": seed, "client_id": f"C-{seed}"}]
            ),
            "dim_date": self.spark.createDataFrame(
                [{"date_key": seed, "date_day": f"2026-08-{seed:02d}"}]
            ),
            "dim_fault": self.spark.createDataFrame(
                [{"fault_key": seed, "fault_code": f"F-{seed}"}]
            ),
            "dim_machine": self.spark.createDataFrame(
                [{"machine_key": seed, "machine_id": f"M-{seed}"}]
            ),
            "dim_model": self.spark.createDataFrame(
                [{"model_key": seed, "model": f"MODEL-{seed}"}]
            ),
            "dim_site": self.spark.createDataFrame(
                [{"site_key": seed, "site_id": f"S-{seed}"}]
            ),
            "fact_machine_failure_event": self.spark.createDataFrame(
                [{"event_id": f"E-{seed}", "downtime_minutes": seed}]
            ),
            "fact_machine_uptime_daily": self.spark.createDataFrame(
                [{"date_key": seed, "machine_key": seed, "uptime_pct": float(seed)}]
            ),
        }
        return with_publication_run_id(
            raw,
            publication_run_id=run_id,
            run_id_column=self.run_id_column,
        )

    def started_manifest(self, frames, run_id, hour):
        return build_family_manifest(
            frames,
            publication_family="warehouse",
            publication_run_id=run_id,
            run_id_column=self.run_id_column,
            publication_started_at_utc=f"2026-08-23T{hour:02d}:00:00Z",
        )

    def committed_manifest(self, frames, run_id, start_hour, end_hour):
        return transition_family_manifest(
            self.started_manifest(frames, run_id, start_hour),
            publication_state=STATE_COMMITTED,
            publication_completed_at_utc=f"2026-08-23T{end_hour:02d}:00:00Z",
        )

    def histories(self, first, second):
        return {
            dataset: first[dataset].unionByName(second[dataset])
            for dataset in WAREHOUSE_DATASETS
        }

    def test_partial_generation_cannot_mix_dimensions_and_facts(self) -> None:
        old = self.frames("job_old", 10)
        new = self.frames("job_new", 20)
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            self.started_manifest(new, "job_new", 12)
        )

        current = select_latest_committed_frames(
            manifest=manifest,
            histories=self.histories(old, new),
            publication_family="warehouse",
            run_id_column=self.run_id_column,
        )

        for dataset, frame in current.items():
            with self.subTest(dataset=dataset):
                self.assertEqual(
                    {"job_old"},
                    {
                        row[self.run_id_column]
                        for row in frame.select(self.run_id_column).distinct().collect()
                    },
                )

    def test_new_commit_switches_all_eight_warehouse_outputs_together(self) -> None:
        old = self.frames("job_old", 10)
        new = self.frames("job_new", 20)
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            self.committed_manifest(new, "job_new", 12, 13)
        )

        current = select_latest_committed_frames(
            manifest=manifest,
            histories=self.histories(old, new),
            publication_family="warehouse",
            run_id_column=self.run_id_column,
        )

        self.assertEqual(set(WAREHOUSE_DATASETS), set(current))
        for dataset, frame in current.items():
            with self.subTest(dataset=dataset):
                self.assertEqual(
                    {"job_new"},
                    {
                        row[self.run_id_column]
                        for row in frame.select(self.run_id_column).distinct().collect()
                    },
                )

    def test_same_count_fact_corruption_is_detected(self) -> None:
        frames = self.frames("job_new", 30)
        manifest = self.committed_manifest(frames, "job_new", 14, 15)
        corrupted = dict(frames)
        corrupted["fact_machine_failure_event"] = frames[
            "fact_machine_failure_event"
        ].withColumn("downtime_minutes", F.lit(999))

        findings = audit_family_publication(
            manifest=manifest,
            histories=corrupted,
            publication_family="warehouse",
            publication_run_id="job_new",
            run_id_column=self.run_id_column,
        )

        self.assertIn(
            ("history_payload_mismatch", "fact_machine_failure_event"),
            {(finding.code, finding.dataset) for finding in findings},
        )

    def test_missing_dimension_history_is_detected(self) -> None:
        frames = self.frames("job_new", 40)
        manifest = self.committed_manifest(frames, "job_new", 16, 17)
        incomplete = {
            dataset: frame
            for dataset, frame in frames.items()
            if dataset != "dim_fault"
        }

        findings = audit_family_publication(
            manifest=manifest,
            histories=incomplete,
            publication_family="warehouse",
            publication_run_id="job_new",
            run_id_column=self.run_id_column,
        )

        self.assertIn(
            "history_dataset_mismatch",
            {finding.code for finding in findings},
        )


if __name__ == "__main__":
    unittest.main()
