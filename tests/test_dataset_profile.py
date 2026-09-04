import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.azure_ingestion import MACHINE_EVENT_COLUMNS
from lakehouse_demo.dataset_profile import (
    DatasetProfileError,
    default_machine_event_sources,
    profile_machine_event_files,
    render_dataset_profile_markdown,
    write_dataset_profile_package,
)


def make_row(
    event_id: str,
    timestamp: str,
    *,
    machine: str = "MCH-1",
    status: str = "RUNNING",
    event_type: str = "telemetry",
    fault: str = "OK",
    part: str = "NONE",
    duration: str = "60",
    downtime: str = "0",
    cost: str = "0",
    quantity: str = "0",
) -> list[str]:
    return [
        event_id,
        machine,
        timestamp,
        "SITE-A",
        "CLIENT-A",
        "Excavator",
        "1.0",
        event_type,
        status,
        fault,
        "none",
        "70.0",
        "2.0",
        "80",
        duration,
        downtime,
        cost,
        part,
        quantity,
        "day",
    ]


def write_csv(
    path: Path,
    rows: list[list[str]],
    header: tuple[str, ...] = MACHINE_EVENT_COLUMNS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ",".join(header) + "\n" + "\n".join(",".join(row) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")


class DatasetProfileTest(unittest.TestCase):
    def test_committed_fixtures_have_expected_profile(self):
        profile = profile_machine_event_files(
            ROOT, default_machine_event_sources(ROOT)
        )
        self.assertEqual(2, profile["input"]["file_count"])
        self.assertEqual(31, profile["rows"]["physical_row_count"])
        self.assertEqual(30, profile["rows"]["unique_event_id_count"])
        self.assertEqual(1, profile["rows"]["replay_duplicate_row_count"])
        self.assertEqual(6, profile["coverage"]["machine_count"])
        self.assertEqual(4, profile["coverage"]["client_count"])
        self.assertEqual(4, profile["coverage"]["site_count"])
        self.assertEqual(6, profile["coverage"]["model_count"])
        self.assertEqual(
            "2026-04-01T06:00:00Z", profile["coverage"]["observation_start"]
        )
        self.assertEqual(
            "2026-04-03T07:00:00Z", profile["coverage"]["observation_end"]
        )
        self.assertEqual(
            {"FAULT": 6, "IDLE": 3, "MAINTENANCE": 5, "RUNNING": 16},
            profile["operations"]["status_counts"],
        )
        self.assertEqual(1355, profile["operations"]["duration_minutes_total"])
        self.assertEqual(335, profile["operations"]["downtime_minutes_total"])
        self.assertEqual(
            "4230", profile["operations"]["maintenance_cost_gbp_total"]
        )
        self.assertEqual(17, profile["operations"]["part_quantity_total"])

    def test_profiles_multiple_files_in_deterministic_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = make_row("E1", "2026-04-01T06:00:00Z")
            fault = make_row(
                "E2",
                "2026-04-02T06:00:00Z",
                machine="MCH-2",
                status="FAULT",
                event_type="alert",
                fault="F1",
                part="P1",
                duration="20",
                downtime="40",
                cost="125.50",
                quantity="2",
            )
            write_csv(root / "data" / "b.csv", [fault])
            write_csv(root / "data" / "a.csv", [first, first])

            profile = profile_machine_event_files(root, ["data/b.csv", "data/a.csv"])

            self.assertEqual("synthetic_machine_event_profile", profile["profile_kind"])
            self.assertEqual("repository_source_only", profile["evidence_boundary"])
            self.assertEqual(3, profile["rows"]["physical_row_count"])
            self.assertEqual(2, profile["rows"]["unique_event_id_count"])
            self.assertEqual(1, profile["rows"]["replay_duplicate_row_count"])
            self.assertEqual(2, profile["coverage"]["machine_count"])
            self.assertEqual(1, profile["operations"]["fault_event_row_count"])
            self.assertEqual(80, profile["operations"]["duration_minutes_total"])
            self.assertEqual(
                "unique_event_id_first_observation",
                profile["operations"]["aggregate_grain"],
            )
            self.assertEqual(
                "125.5", profile["operations"]["maintenance_cost_gbp_total"]
            )
            self.assertEqual(
                ["data/a.csv", "data/b.csv"],
                [item["path"] for item in profile["input"]["files"]],
            )

    def test_conflicting_duplicate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = make_row("E1", "2026-04-01T06:00:00Z")
            changed = make_row("E1", "2026-04-01T07:00:00Z")
            write_csv(root / "data.csv", [first, changed])
            with self.assertRaises(DatasetProfileError) as raised:
                profile_machine_event_files(root, ["data.csv"])
            self.assertEqual("machine_event_validation_failed", raised.exception.category)
            self.assertIn("conflicting_duplicate", raised.exception.details)
            self.assertNotIn("2026-04-01", str(raised.exception))

    def test_invalid_header_fails_before_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(
                root / "data.csv",
                [make_row("E1", "2026-04-01T06:00:00Z")],
                ("bad",),
            )
            with self.assertRaises(DatasetProfileError) as raised:
                profile_machine_event_files(root, ["data.csv"])
            self.assertIn("header_mismatch", raised.exception.details)

    def test_outside_root_and_symlink_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            outside = base / "outside.csv"
            write_csv(outside, [make_row("E1", "2026-04-01T06:00:00Z")])
            with self.assertRaises(DatasetProfileError) as outside_error:
                profile_machine_event_files(root, [outside])
            self.assertEqual(
                "repository_file_outside_root", outside_error.exception.category
            )

            link = root / "link.csv"
            os.symlink(outside, link)
            with self.assertRaises(DatasetProfileError) as link_error:
                profile_machine_event_files(root, [link])
            self.assertEqual("repository_file_symlink", link_error.exception.category)

    def test_package_is_deterministic_and_non_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(
                root / "data.csv", [make_row("E1", "2026-04-01T06:00:00Z")]
            )
            profile = profile_machine_event_files(root, ["data.csv"])
            output = root / "evidence"
            write_dataset_profile_package(profile, output)
            parsed = json.loads(
                (output / "dataset-profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(profile, parsed)
            markdown = (output / "dataset-profile.md").read_text(encoding="utf-8")
            self.assertEqual(render_dataset_profile_markdown(profile), markdown)
            self.assertIn("does not prove", markdown)
            with self.assertRaises(DatasetProfileError) as raised:
                write_dataset_profile_package(profile, output)
            self.assertEqual("output_directory_exists", raised.exception.category)

    def test_default_sources_include_sorted_increments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(
                root / "data/sample_machine_events.csv",
                [make_row("E1", "2026-04-01T06:00:00Z")],
            )
            write_csv(
                root / "data/increments/z.csv",
                [make_row("E3", "2026-04-03T06:00:00Z")],
            )
            write_csv(
                root / "data/increments/a.csv",
                [make_row("E2", "2026-04-02T06:00:00Z")],
            )
            self.assertEqual(
                (
                    "data/sample_machine_events.csv",
                    "data/increments/a.csv",
                    "data/increments/z.csv",
                ),
                default_machine_event_sources(root),
            )


if __name__ == "__main__":
    unittest.main()
