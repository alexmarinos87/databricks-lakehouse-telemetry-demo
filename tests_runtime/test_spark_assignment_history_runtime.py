from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.spark_assignment_history import (  # noqa: E402
    SEMANTIC_VERSION,
    audit_assignment_history,
    build_assignment_history,
    resolve_assignment_as_of,
)


class SparkAssignmentHistoryRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("lakehouse-demo-assignment-history-runtime-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def source(self, rows):
        return self.spark.createDataFrame(rows)

    def clean_history(self):
        evidence = self.source(
            [
                {
                    "event_date": date(2026, 4, 1),
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": "site-1",
                    "model": "model-X",
                },
                {
                    "event_date": date(2026, 4, 3),
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": "site-1",
                    "model": "model-X",
                },
                {
                    "event_date": date(2026, 4, 5),
                    "machine_id": "M-1",
                    "client_id": "client-B",
                    "site_id": "site-2",
                    "model": "model-X",
                },
            ]
        )
        result = build_assignment_history({"uptime": evidence})
        self.assertTrue(result.can_publish)
        return result.history

    def test_reassignment_creates_non_overlapping_effective_periods(self) -> None:
        history = self.clean_history()
        rows = history.orderBy("effective_from").collect()

        self.assertEqual(2, len(rows))
        self.assertEqual(date(2026, 4, 1), rows[0]["effective_from"])
        self.assertEqual(date(2026, 4, 4), rows[0]["effective_to"])
        self.assertFalse(rows[0]["is_current"])
        self.assertEqual("client-A", rows[0]["client_id"])
        self.assertEqual(date(2026, 4, 5), rows[1]["effective_from"])
        self.assertIsNone(rows[1]["effective_to"])
        self.assertTrue(rows[1]["is_current"])
        self.assertEqual("client-B", rows[1]["client_id"])
        self.assertEqual(SEMANTIC_VERSION, rows[1]["assignment_semantics_version"])

    def test_consistent_uptime_and_failure_evidence_has_no_precedence(self) -> None:
        row = {
            "event_date": date(2026, 4, 1),
            "machine_id": "M-1",
            "client_id": "client-A",
            "site_id": "site-1",
            "model": "model-X",
        }
        result = build_assignment_history(
            {"uptime": self.source([row]), "failure": self.source([row])}
        )

        self.assertTrue(result.can_publish)
        history_row = result.history.collect()[0]
        self.assertEqual(2, history_row["assignment_evidence_source_count"])

    def test_same_day_conflict_is_rejected_without_selecting_a_winner(self) -> None:
        uptime = self.source(
            [
                {
                    "event_date": date(2026, 4, 1),
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": "site-1",
                    "model": "model-X",
                }
            ]
        )
        failure = self.source(
            [
                {
                    "event_date": date(2026, 4, 1),
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": "site-2",
                    "model": "model-X",
                }
            ]
        )

        result = build_assignment_history({"uptime": uptime, "failure": failure})

        self.assertFalse(result.can_publish)
        self.assertIn(
            "same_day_assignment_conflict",
            {finding.code for finding in result.findings},
        )
        self.assertEqual(0, result.history.count())
        self.assertEqual(2, result.rejected_evidence.count())

    def test_as_of_resolution_uses_the_effective_assignment(self) -> None:
        history = self.clean_history()
        events = self.source(
            [
                {"event_id": "E-1", "event_date": date(2026, 4, 3), "machine_id": "M-1"},
                {"event_id": "E-2", "event_date": date(2026, 4, 6), "machine_id": "M-1"},
            ]
        )

        result = resolve_assignment_as_of(events, history)
        rows = {
            row["event_id"]: row for row in result.resolved.collect()
        }

        self.assertTrue(result.can_publish)
        self.assertEqual(2, len(rows))
        self.assertEqual("client-A", rows["E-1"]["resolved_client_id"])
        self.assertEqual("site-1", rows["E-1"]["resolved_site_id"])
        self.assertEqual("client-B", rows["E-2"]["resolved_client_id"])
        self.assertEqual("site-2", rows["E-2"]["resolved_site_id"])
        self.assertEqual(0, result.unresolved.count())
        self.assertEqual(0, result.ambiguous.count())

    def test_unresolved_event_is_preserved_without_unknown_member(self) -> None:
        history = self.clean_history()
        events = self.source(
            [
                {"event_id": "E-0", "event_date": date(2026, 3, 30), "machine_id": "M-1"}
            ]
        )

        result = resolve_assignment_as_of(events, history)
        row = result.unresolved.collect()[0]

        self.assertFalse(result.can_publish)
        self.assertEqual("E-0", row["event_id"])
        self.assertIsNone(row["resolved_client_id"])
        self.assertIsNone(row["resolved_site_id"])
        self.assertIn(
            "assignment_unresolved", {finding.code for finding in result.findings}
        )
        self.assertNotIn(
            "assignment_resolution_count_mismatch",
            {finding.code for finding in result.findings},
        )

    def test_late_assignment_rebuild_resolves_previously_unresolved_event(self) -> None:
        late_fact = self.source(
            [
                {"event_id": "E-late", "event_date": date(2026, 4, 3), "machine_id": "M-1"}
            ]
        )
        initial = self.source(
            [
                {
                    "event_date": date(2026, 4, 5),
                    "machine_id": "M-1",
                    "client_id": "client-B",
                    "site_id": "site-2",
                    "model": "model-X",
                }
            ]
        )
        initial_history = build_assignment_history({"uptime": initial}).history
        self.assertEqual(
            1,
            resolve_assignment_as_of(late_fact, initial_history).unresolved.count(),
        )

        rebuilt = self.source(
            [
                {
                    "event_date": date(2026, 4, 1),
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": "site-1",
                    "model": "model-X",
                },
                {
                    "event_date": date(2026, 4, 5),
                    "machine_id": "M-1",
                    "client_id": "client-B",
                    "site_id": "site-2",
                    "model": "model-X",
                },
            ]
        )
        rebuilt_history = build_assignment_history({"uptime": rebuilt}).history
        result = resolve_assignment_as_of(late_fact, rebuilt_history)

        self.assertTrue(result.can_publish)
        self.assertEqual("client-A", result.resolved.collect()[0]["resolved_client_id"])

    def test_overlapping_history_is_detected_and_resolution_is_ambiguous(self) -> None:
        history = self.source(
            [
                {
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": "site-1",
                    "model": "model-X",
                    "effective_from": date(2026, 4, 1),
                    "effective_to": date(2026, 4, 10),
                    "is_current": False,
                    "assignment_evidence_source_count": 1,
                    "assignment_semantics_version": SEMANTIC_VERSION,
                },
                {
                    "machine_id": "M-1",
                    "client_id": "client-B",
                    "site_id": "site-2",
                    "model": "model-X",
                    "effective_from": date(2026, 4, 5),
                    "effective_to": None,
                    "is_current": True,
                    "assignment_evidence_source_count": 1,
                    "assignment_semantics_version": SEMANTIC_VERSION,
                },
            ]
        )
        events = self.source(
            [
                {"event_id": "E-overlap", "event_date": date(2026, 4, 6), "machine_id": "M-1"}
            ]
        )

        audit_codes = {finding.code for finding in audit_assignment_history(history)}
        resolution = resolve_assignment_as_of(events, history)

        self.assertIn("assignment_history_range_overlap", audit_codes)
        self.assertIn(
            "assignment_ambiguous", {finding.code for finding in resolution.findings}
        )
        self.assertEqual(2, resolution.ambiguous.count())
        self.assertNotIn(
            "assignment_resolution_count_mismatch",
            {finding.code for finding in resolution.findings},
        )

    def test_missing_assignment_identity_blocks_history(self) -> None:
        evidence = self.source(
            [
                {
                    "event_date": date(2026, 4, 1),
                    "machine_id": "M-1",
                    "client_id": "client-A",
                    "site_id": " ",
                    "model": "model-X",
                }
            ]
        )

        result = build_assignment_history({"uptime": evidence})

        self.assertFalse(result.can_publish)
        self.assertIn(
            "missing_assignment_identity",
            {finding.code for finding in result.findings},
        )
        self.assertEqual(1, result.rejected_evidence.count())


if __name__ == "__main__":
    unittest.main()
