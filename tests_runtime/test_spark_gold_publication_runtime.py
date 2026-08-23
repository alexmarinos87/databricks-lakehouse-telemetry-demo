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
    STATE_STARTED,
    audit_family_publication,
    build_family_manifest,
    select_latest_committed_frames,
    transition_family_manifest,
    with_publication_run_id,
)


GOLD_DATASETS = (
    "gold_machine_uptime",
    "gold_failure_events",
    "gold_maintenance_costs",
    "gold_parts_usage",
    "gold_client_asset_summary",
)


class SparkGoldPublicationRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-gold-publication-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")
        cls.run_id_column = "gold_publication_run_id"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def frames(self, run_id: str, seed: int, *, empty_parts: bool = False):
        raw = {
            "gold_machine_uptime": self.spark.createDataFrame(
                [{"machine_id": f"M-{seed}", "uptime_pct": float(seed)}]
            ),
            "gold_failure_events": self.spark.createDataFrame(
                [{"event_id": f"E-{seed}", "downtime_minutes": seed}]
            ),
            "gold_maintenance_costs": self.spark.createDataFrame(
                [{"site_id": f"S-{seed}", "maintenance_cost_gbp": float(seed)}]
            ),
            "gold_parts_usage": (
                self.spark.createDataFrame([], "part_code string, part_quantity long")
                if empty_parts
                else self.spark.createDataFrame(
                    [{"part_code": f"P-{seed}", "part_quantity": seed}]
                )
            ),
            "gold_client_asset_summary": self.spark.createDataFrame(
                [{"client_id": f"C-{seed}", "failure_event_count": seed}]
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
            publication_family="gold",
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
            for dataset in GOLD_DATASETS
        }

    def test_started_gold_generation_does_not_mix_current_outputs(self) -> None:
        old = self.frames("job_old", 10)
        new = self.frames("job_new", 20)
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            self.started_manifest(new, "job_new", 12)
        )

        current = select_latest_committed_frames(
            manifest=manifest,
            histories=self.histories(old, new),
            publication_family="gold",
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

    def test_new_commit_switches_all_five_gold_datasets_together(self) -> None:
        old = self.frames("job_old", 10)
        new = self.frames("job_new", 20)
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            self.committed_manifest(new, "job_new", 12, 13)
        )

        current = select_latest_committed_frames(
            manifest=manifest,
            histories=self.histories(old, new),
            publication_family="gold",
            run_id_column=self.run_id_column,
        )

        self.assertEqual(set(GOLD_DATASETS), set(current))
        for dataset, frame in current.items():
            with self.subTest(dataset=dataset):
                self.assertEqual(
                    {"job_new"},
                    {
                        row[self.run_id_column]
                        for row in frame.select(self.run_id_column).distinct().collect()
                    },
                )

    def test_same_count_corruption_in_one_gold_output_blocks_commit(self) -> None:
        frames = self.frames("job_new", 20)
        manifest = self.committed_manifest(frames, "job_new", 12, 13)
        corrupted = dict(frames)
        corrupted["gold_maintenance_costs"] = frames[
            "gold_maintenance_costs"
        ].withColumn("maintenance_cost_gbp", F.lit(999.0))

        findings = audit_family_publication(
            manifest=manifest,
            histories=corrupted,
            publication_family="gold",
            publication_run_id="job_new",
            run_id_column=self.run_id_column,
        )

        self.assertIn(
            ("history_payload_mismatch", "gold_maintenance_costs"),
            {(finding.code, finding.dataset) for finding in findings},
        )
        self.assertNotIn(
            "gold_machine_uptime",
            {
                finding.dataset
                for finding in findings
                if finding.code == "history_payload_mismatch"
            },
        )

    def test_empty_optional_gold_output_is_valid_and_remains_empty(self) -> None:
        frames = self.frames("job_empty_parts", 30, empty_parts=True)
        manifest = self.committed_manifest(frames, "job_empty_parts", 14, 15)

        findings = audit_family_publication(
            manifest=manifest,
            histories=frames,
            publication_family="gold",
            publication_run_id="job_empty_parts",
            run_id_column=self.run_id_column,
        )
        current = select_latest_committed_frames(
            manifest=manifest,
            histories=frames,
            publication_family="gold",
            run_id_column=self.run_id_column,
        )

        self.assertEqual((), findings)
        self.assertEqual(0, current["gold_parts_usage"].count())

    def test_history_dataset_omission_is_detected(self) -> None:
        frames = self.frames("job_new", 40)
        manifest = self.committed_manifest(frames, "job_new", 16, 17)
        incomplete_histories = {
            dataset: frame
            for dataset, frame in frames.items()
            if dataset != "gold_failure_events"
        }

        findings = audit_family_publication(
            manifest=manifest,
            histories=incomplete_histories,
            publication_family="gold",
            publication_run_id="job_new",
            run_id_column=self.run_id_column,
        )

        self.assertIn(
            "history_dataset_mismatch",
            {finding.code for finding in findings},
        )


if __name__ == "__main__":
    unittest.main()
