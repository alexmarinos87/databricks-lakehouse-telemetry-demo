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
    STATE_FAILED,
    STATE_STARTED,
    audit_family_publication,
    build_family_manifest,
    latest_committed_run_id,
    publication_state_for_run,
    select_latest_committed_frames,
    transition_family_manifest,
    with_publication_run_id,
)


class SparkFamilyPublicationRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-family-publication-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")
        cls.run_id_column = "silver_publication_run_id"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def frames(self, run_id: str, value: int, *, quarantine: bool = True):
        raw = {
            "silver": self.spark.createDataFrame(
                [
                    {"event_id": f"E-{value}", "metric": value},
                    {"event_id": f"E-{value + 1}", "metric": value + 1},
                ]
            ),
            "quarantine": (
                self.spark.createDataFrame(
                    [{"event_id": f"Q-{value}", "reason": "invalid"}]
                )
                if quarantine
                else self.spark.createDataFrame(
                    [],
                    "event_id string, reason string",
                )
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
            publication_family="silver",
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

    def combined_histories(self, first, second):
        return {
            dataset: first[dataset].unionByName(second[dataset])
            for dataset in first
        }

    def test_started_generation_does_not_replace_previous_commit(self) -> None:
        old = self.frames("job_old", 10)
        new = self.frames("job_new", 20)
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            self.started_manifest(new, "job_new", 12)
        )

        current = select_latest_committed_frames(
            manifest=manifest,
            histories=self.combined_histories(old, new),
            publication_family="silver",
            run_id_column=self.run_id_column,
        )

        self.assertEqual(
            {"job_old"},
            {
                row[self.run_id_column]
                for row in current["silver"]
                .select(self.run_id_column)
                .distinct()
                .collect()
            },
        )

    def test_newer_committed_generation_is_selected_for_every_dataset(self) -> None:
        old = self.frames("job_old", 10)
        new = self.frames("job_new", 20)
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            self.committed_manifest(new, "job_new", 12, 13)
        )

        current = select_latest_committed_frames(
            manifest=manifest,
            histories=self.combined_histories(old, new),
            publication_family="silver",
            run_id_column=self.run_id_column,
        )

        for dataset_name, frame in current.items():
            with self.subTest(dataset=dataset_name):
                self.assertEqual(
                    {"job_new"},
                    {
                        row[self.run_id_column]
                        for row in frame.select(self.run_id_column).distinct().collect()
                    },
                )

    def test_same_count_payload_corruption_is_detected(self) -> None:
        frames = self.frames("job_new", 20)
        manifest = self.committed_manifest(frames, "job_new", 12, 13)
        corrupted = dict(frames)
        corrupted["silver"] = frames["silver"].withColumn(
            "metric",
            F.when(F.col("event_id") == "E-20", F.lit(999)).otherwise(
                F.col("metric")
            ),
        )

        findings = audit_family_publication(
            manifest=manifest,
            histories=corrupted,
            publication_family="silver",
            publication_run_id="job_new",
            run_id_column=self.run_id_column,
        )

        self.assertIn(
            ("history_payload_mismatch", "silver"),
            {(finding.code, finding.dataset) for finding in findings},
        )

    def test_empty_quarantine_is_a_valid_committed_dataset(self) -> None:
        frames = self.frames("job_clean", 30, quarantine=False)
        manifest = self.committed_manifest(frames, "job_clean", 14, 15)

        findings = audit_family_publication(
            manifest=manifest,
            histories=frames,
            publication_family="silver",
            publication_run_id="job_clean",
            run_id_column=self.run_id_column,
        )

        self.assertEqual((), findings)
        self.assertEqual(0, frames["quarantine"].count())

    def test_failed_generation_is_not_current(self) -> None:
        old = self.frames("job_old", 10)
        failed = self.frames("job_failed", 40)
        failed_manifest = transition_family_manifest(
            self.started_manifest(failed, "job_failed", 16),
            publication_state=STATE_FAILED,
            publication_completed_at_utc="2026-08-23T17:00:00Z",
            failure_code="conflicting_event_ids",
        )
        manifest = self.committed_manifest(old, "job_old", 10, 11).unionByName(
            failed_manifest
        )

        self.assertEqual(
            "job_old",
            latest_committed_run_id(
                manifest,
                publication_family="silver",
            ),
        )
        self.assertEqual(
            STATE_FAILED,
            publication_state_for_run(
                manifest,
                publication_family="silver",
                publication_run_id="job_failed",
            ),
        )

    def test_duplicate_manifest_run_fails_closed(self) -> None:
        frames = self.frames("job_duplicate", 50)
        committed = self.committed_manifest(frames, "job_duplicate", 18, 19)
        duplicate_manifest = committed.unionByName(committed)

        with self.assertRaisesRegex(ValueError, "duplicate publication run"):
            latest_committed_run_id(
                duplicate_manifest,
                publication_family="silver",
            )

    def test_invalid_terminal_transition_fails_closed(self) -> None:
        frames = self.frames("job_invalid", 60)
        started = self.started_manifest(frames, "job_invalid", 20)

        with self.assertRaisesRegex(ValueError, "requires completion time"):
            transition_family_manifest(
                started,
                publication_state=STATE_COMMITTED,
            )
        with self.assertRaisesRegex(ValueError, "failure_code"):
            transition_family_manifest(
                started,
                publication_state=STATE_FAILED,
                publication_completed_at_utc="2026-08-23T21:00:00Z",
            )
        self.assertEqual(
            STATE_STARTED,
            publication_state_for_run(
                started,
                publication_family="silver",
                publication_run_id="job_invalid",
            ),
        )


if __name__ == "__main__":
    unittest.main()
