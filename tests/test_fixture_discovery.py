"""Behavioral discovery bounds and real callers; no glob-source assertions."""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lakehouse_demo import fixture_discovery as subject


class FixtureDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.directory = self.data / "increments"

    def assert_category(self, category):
        with self.assertRaises(subject.FixtureDiscoveryError) as caught:
            subject.discover_increment_paths(self.root)
        self.assertEqual(category, caught.exception.category)
        self.assertEqual(category, str(caught.exception))

    def test_missing_and_empty_optional_directory_are_compatible(self):
        self.assertEqual((), subject.discover_increment_paths(self.root))
        self.directory.mkdir()
        self.assertEqual((), subject.discover_increment_paths(self.root))

    def test_sorted_immediate_selection_ignores_unrelated_names(self):
        self.directory.mkdir()
        for name in ("z.csv", "a.csv", "README.md"):
            (self.directory / name).write_text("synthetic")
        (self.directory / "nested").mkdir()
        (self.directory / "nested/ignored.csv").write_text("synthetic")
        self.assertEqual(("data/increments/a.csv", "data/increments/z.csv"),
                         subject.discover_increment_paths(self.root))

    def test_existing_regular_file_is_not_an_empty_directory(self):
        self.directory.write_text("synthetic invalid layout")
        self.assert_category("fixture_directory_not_directory")

    def test_missing_required_data_directory_is_rejected(self):
        self.data.rmdir()
        self.assert_category("fixture_directory_unavailable")

    def test_regular_file_data_parent_is_rejected(self):
        self.data.rmdir()
        self.data.write_text("synthetic")
        self.assert_category("fixture_directory_not_directory")

    def test_empty_and_dangling_symlink_directories_are_rejected(self):
        target = self.root / "target"
        target.mkdir()
        self.directory.symlink_to(target, target_is_directory=True)
        self.assert_category("fixture_directory_symlink")
        target.rmdir()
        self.assert_category("fixture_directory_symlink")

    def test_symlink_data_parent_is_rejected(self):
        self.data.rmdir()
        target = self.root / "target"
        target.mkdir()
        self.data.symlink_to(target, target_is_directory=True)
        self.assert_category("fixture_directory_symlink")

    def test_directory_open_error_is_sanitized(self):
        self.directory.mkdir()
        with mock.patch.object(subject.os, "scandir", side_effect=PermissionError("PRIVATE_MARKER")):
            self.assert_category("fixture_directory_read_failed")

    def test_iteration_failure_does_not_return_a_partial_selection(self):
        self.directory.mkdir()
        def broken_entries():
            yield SimpleNamespace(name="first.csv")
            raise OSError("PRIVATE_MARKER")
        with mock.patch.object(subject.os, "scandir",
                               return_value=contextlib.nullcontext(broken_entries())):
            self.assert_category("fixture_directory_read_failed")

    def test_scan_budget_includes_nonmatching_names(self):
        self.directory.mkdir()
        for number in range(3):
            (self.directory / f"ignored{number}.txt").touch()
        with mock.patch.object(subject, "MAX_DIRECTORY_ENTRIES", 2):
            self.assert_category("fixture_directory_entry_limit_exceeded")

    def test_matching_file_budget_is_bounded(self):
        self.directory.mkdir()
        for number in range(3):
            (self.directory / f"input{number}.csv").touch()
        with mock.patch.object(subject, "MAX_INCREMENT_FILES", 2):
            self.assert_category("repository_file_count_exceeded")

    def test_scan_stops_after_one_over_budget_entry(self):
        self.directory.mkdir()
        consumed = []
        def many_entries():
            for number in range(100):
                consumed.append(number)
                yield SimpleNamespace(name=f"ignored{number}.txt")
        with mock.patch.object(subject, "MAX_DIRECTORY_ENTRIES", 2), mock.patch.object(
                subject.os, "scandir", return_value=contextlib.nullcontext(many_entries())):
            self.assert_category("fixture_directory_entry_limit_exceeded")
        self.assertEqual([0, 1, 2], consumed)

    def test_replaced_directory_is_detected_after_scan(self):
        self.directory.mkdir()
        (self.directory / "first.csv").touch()
        original = os.scandir
        @contextlib.contextmanager
        def replace_after_scan(path):
            with original(path) as entries:
                yield entries
            self.directory.rename(self.root / "displaced")
            self.directory.mkdir()
        with mock.patch.object(subject.os, "scandir", replace_after_scan):
            self.assert_category("fixture_directory_changed")

    def test_disappearing_directory_is_not_relabelled_optional(self):
        self.directory.mkdir()
        original = os.scandir
        @contextlib.contextmanager
        def remove_after_scan(path):
            with original(path) as entries:
                yield entries
            self.directory.rmdir()
        with mock.patch.object(subject.os, "scandir", remove_after_scan):
            self.assert_category("fixture_directory_changed")


class FixtureDiscoveryIntegrationTest(unittest.TestCase):
    def test_both_selectors_reject_malformed_optional_directory(self):
        from lakehouse_demo.dataset_profile import DatasetProfileError, default_machine_event_sources
        from lakehouse_demo.portfolio_snapshot import PortfolioSnapshotError, build_portfolio_snapshot
        from test_portfolio_snapshot import copy_sources
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            shutil.rmtree(root / "data/increments")
            (root / "data/increments").write_text("PRIVATE_MARKER")
            for operation, error in ((lambda: default_machine_event_sources(root), DatasetProfileError),
                                     (lambda: build_portfolio_snapshot(root), PortfolioSnapshotError)):
                with self.subTest(error=error.__name__), self.assertRaises(error) as caught:
                    operation()
                self.assertEqual("fixture_directory_not_directory", caught.exception.category)

    def test_optional_absence_still_builds_sample_only_evidence(self):
        from lakehouse_demo.portfolio_snapshot import build_portfolio_snapshot
        from test_portfolio_snapshot import copy_sources
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            shutil.rmtree(root / "data/increments")
            result = build_portfolio_snapshot(root)
            self.assertEqual(1, result["dataset_profile"]["input"]["file_count"])
            self.assertEqual("repository_source_only", result["evidence_boundary"])

    def test_both_real_clis_fail_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            (root / "data").mkdir(parents=True)
            (root / "data/increments").write_text("PRIVATE_MARKER")
            for number, script in enumerate(("profile_synthetic_dataset.py", "build_portfolio_snapshot.py")):
                output = Path(temporary) / f"out{number}"
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / script), "--repository-root", str(root),
                     "--output-dir", str(output)], capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertEqual("", result.stdout)
                self.assertEqual("fixture_directory_not_directory", json.loads(result.stderr)["category"])
                self.assertNotIn("PRIVATE_MARKER", result.stderr)
                self.assertNotIn(str(root), result.stderr)
                self.assertFalse(output.exists())

    def test_committed_profile_and_discovery_source_provenance(self):
        from lakehouse_demo.portfolio_snapshot import build_portfolio_snapshot
        result = build_portfolio_snapshot(ROOT)
        self.assertEqual(31, result["dataset_profile"]["rows"]["physical_row_count"])
        self.assertEqual(30, result["dataset_profile"]["rows"]["unique_event_id_count"])
        self.assertEqual("4230", result["dataset_profile"]["operations"]["maintenance_cost_gbp_total"])
        self.assertIn("src/lakehouse_demo/fixture_discovery.py", {item["path"] for item in result["sources"]})

    def test_scan_error_reaches_both_public_error_types(self):
        from lakehouse_demo.dataset_profile import DatasetProfileError, default_machine_event_sources
        from lakehouse_demo.portfolio_snapshot import PortfolioSnapshotError, build_portfolio_snapshot
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data/increments").mkdir(parents=True)
            with mock.patch.object(subject.os, "scandir", side_effect=PermissionError("PRIVATE_MARKER")):
                for operation, error in ((lambda: default_machine_event_sources(root), DatasetProfileError),
                                         (lambda: build_portfolio_snapshot(root), PortfolioSnapshotError)):
                    with self.subTest(error=error.__name__), self.assertRaises(error) as caught:
                        operation()
                    self.assertEqual("fixture_directory_read_failed", caught.exception.category)


if __name__ == "__main__":
    unittest.main()
