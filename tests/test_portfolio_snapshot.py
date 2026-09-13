"""Behavioural evidence for the offline portfolio snapshot and its public CLI."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo import portfolio_snapshot as subject
from lakehouse_demo.dataset_profile import default_machine_event_sources


def copy_sources(root):
    manifest = json.loads((ROOT / subject.MANIFEST_PATH).read_text(encoding="utf-8"))
    paths = [*subject.EVIDENCE_PATHS, subject.MANIFEST_PATH,
             *default_machine_event_sources(ROOT),
             *(f"sql/reporting_assets/{asset['file']}" for asset in manifest)]
    for relative in paths:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return root


def load_cli():
    spec = importlib.util.spec_from_file_location("snapshot_cli", ROOT / "scripts/build_portfolio_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortfolioSnapshotTest(unittest.TestCase):
    def assert_category(self, expected, operation):
        with self.assertRaises(subject.PortfolioSnapshotError) as raised:
            operation()
        self.assertEqual(expected, raised.exception.category)
        self.assertEqual(expected, str(raised.exception))

    def test_actual_repository_profile_catalogue_and_boundary(self):
        result = subject.build_portfolio_snapshot(ROOT)
        self.assertEqual("repository_source_only", result["evidence_boundary"])
        profile = result["dataset_profile"]
        self.assertEqual(31, profile["rows"]["physical_row_count"])
        self.assertEqual(30, profile["rows"]["unique_event_id_count"])
        self.assertEqual(1, profile["rows"]["replay_duplicate_row_count"])
        self.assertEqual(6, profile["coverage"]["machine_count"])
        self.assertEqual(1355, profile["operations"]["duration_minutes_total"])
        self.assertEqual(335, profile["operations"]["downtime_minutes_total"])
        self.assertEqual("4230", profile["operations"]["maintenance_cost_gbp_total"])
        self.assertEqual(15, result["reporting"]["asset_count"])
        self.assertEqual("passed", result["verification"]["machine_event_contract"])
        self.assertIs(False, result["verification"]["deployment_authorized"])
        for key in ("repository_test_suite", "spark_runtime", "sql_execution", "databricks_runtime"):
            self.assertEqual("not_run", result["verification"][key])
        for key in ("git_commit_provenance", "effective_github_governance"):
            self.assertEqual("not_verified", result["verification"][key])

    def test_source_inventory_and_content_digest_bind_selected_bytes(self):
        result = subject.build_portfolio_snapshot(ROOT)
        paths = [item["path"] for item in result["sources"]]
        self.assertEqual(sorted(set(paths)), paths)
        self.assertTrue(set(subject.EVIDENCE_PATHS).issubset(paths))
        for item in result["sources"]:
            content = (ROOT / item["path"]).read_bytes()
            self.assertEqual(len(content), item["size_bytes"])
            self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
        digest = result.pop("snapshot_sha256")
        content = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), digest)

    def test_repeated_builds_and_packages_are_byte_identical_without_raw_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary) / "source")
            first = subject.build_portfolio_snapshot(root)
            second = subject.build_portfolio_snapshot(root)
            self.assertEqual(first, second)
            outputs = [Path(temporary) / "one", Path(temporary) / "two"]
            for snapshot, output in zip([first, second], outputs):
                subject.write_portfolio_snapshot_package(snapshot, output)
                self.assertEqual({subject.SNAPSHOT_JSON, subject.SNAPSHOT_MARKDOWN},
                                 {path.name for path in output.iterdir()})
            for filename in (subject.SNAPSHOT_JSON, subject.SNAPSHOT_MARKDOWN):
                text = (outputs[0] / filename).read_text(encoding="utf-8")
                self.assertEqual((outputs[0] / filename).read_bytes(), (outputs[1] / filename).read_bytes())
                for private in (str(root), "SELECT ", "CLIENT-A", "event_id,machine_id", "MCH-"):
                    self.assertNotIn(private, text)
            self.assertEqual(first, json.loads((outputs[0] / subject.SNAPSHOT_JSON).read_text()))
            self.assertIn("does not prove", (outputs[0] / subject.SNAPSHOT_MARKDOWN).read_text())

    def test_unselected_files_do_not_affect_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            first = subject.build_portfolio_snapshot(root)
            (root / ".bootstrap").mkdir()
            (root / ".bootstrap/private.json").write_text('{"private":"DO_NOT_COPY"}')
            with mock.patch.dict(os.environ, {"PRIVATE_MARKER": "DO_NOT_COPY"}):
                second = subject.build_portfolio_snapshot(root)
            self.assertEqual(first, second)
            self.assertNotIn("DO_NOT_COPY", json.dumps(second))

    def test_document_change_changes_snapshot_not_business_aggregates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            before = subject.build_portfolio_snapshot(root)
            (root / "README.md").write_text("Changed source narrative\n")
            after = subject.build_portfolio_snapshot(root)
            self.assertNotEqual(before["snapshot_sha256"], after["snapshot_sha256"])
            self.assertEqual(before["dataset_profile"], after["dataset_profile"])

    def test_manifest_shape_keys_and_entry_types_fail_closed(self):
        valid = {"file": "report.sql", "display_name": "Report", "description": "Summary"}
        cases = [{}, [], [valid] * 51, [None], [{**valid, "extra": "private"}],
                 [{"file": "report.sql"}], [{**valid, "description": None}],
                 [{**valid, "display_name": "\nprivate"}], [{**valid, "description": "x" * 2001}]]
        for payload in cases:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(subject.PortfolioSnapshotError):
                    subject._reporting_paths(json.dumps(payload).encode())

    def test_invalid_utf8_json_and_duplicate_json_keys_are_rejected(self):
        for content in (b"\xff", b"[", b'[{"file":"a.sql","file":"b.sql"}]'):
            with self.subTest(content=content):
                with self.assertRaises(subject.PortfolioSnapshotError):
                    subject._reporting_paths(content)

    def test_reporting_paths_reject_traversal_aliases_and_markup(self):
        for filename in ("../private.sql", "/private.sql", "nested/report.sql", "a\\b.sql", "`bad`.sql", "a.sql\n", "..sql"):
            with self.subTest(filename=filename):
                content = json.dumps([{"file": filename, "display_name": "Report", "description": "Summary"}]).encode()
                self.assert_category("reporting_manifest_invalid_entry", lambda: subject._reporting_paths(content))

    def test_duplicate_filenames_or_names_are_rejected(self):
        first = {"file": "a.sql", "display_name": "First", "description": "Summary"}
        for second in ({**first, "display_name": "Second"}, {**first, "file": "b.sql"}):
            self.assert_category("reporting_manifest_duplicate_asset",
                                 lambda: subject._reporting_paths(json.dumps([first, second]).encode()))

    def test_reporting_paths_are_sorted_and_free_text_is_not_exported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            path = root / subject.MANIFEST_PATH
            manifest = json.loads(path.read_text())
            manifest[0]["description"] = "PRIVATE_DESCRIPTION"
            manifest[0]["display_name"] = "PRIVATE_TITLE"
            path.write_text(json.dumps(list(reversed(manifest))))
            result = subject.build_portfolio_snapshot(root)
            paths = [item["path"] for item in result["reporting"]["assets"]]
            self.assertEqual(sorted(paths), paths)
            self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_missing_and_symlink_sql_are_rejected(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as temporary:
                root = copy_sources(Path(temporary))
                sql = root / subject._reporting_paths((root / subject.MANIFEST_PATH).read_bytes())[0]
                sql.unlink()
                if symlink:
                    os.symlink(root / "README.md", sql)
                category = "repository_file_symlink" if symlink else "repository_file_unavailable"
                self.assert_category(category, lambda: subject.build_portfolio_snapshot(root))

    def test_empty_non_utf8_and_oversized_sql_are_rejected(self):
        for content, category in ((b" \n", "reporting_sql_empty"), (b"\xff", "reporting_sql_not_utf8"),
                                  (b"x" * 100_001, "reporting_sql_too_large")):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temporary:
                root = copy_sources(Path(temporary))
                sql = root / subject._reporting_paths((root / subject.MANIFEST_PATH).read_bytes())[0]
                sql.write_bytes(content)
                self.assert_category(category, lambda: subject.build_portfolio_snapshot(root))

    def test_fixture_byte_limit_precedes_business_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            (root / subject.DEFAULT_SAMPLE).write_bytes(b"x" * 2_000_001)
            self.assert_category("repository_file_too_large", lambda: subject.build_portfolio_snapshot(root))

    def test_invalid_fixture_is_not_relabelled_as_passed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            (root / subject.DEFAULT_SAMPLE).write_text("PRIVATE_INVALID_HEADER\n")
            self.assert_category("machine_event_validation_failed", lambda: subject.build_portfolio_snapshot(root))

    def test_input_changed_during_profile_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            original = subject.profile_machine_event_files
            def changed(*args):
                result = original(*args)
                (root / "README.md").write_text("changed during capture")
                return result
            with mock.patch.object(subject, "profile_machine_event_files", changed):
                self.assert_category("repository_file_changed", lambda: subject.build_portfolio_snapshot(root))

    def test_manifest_change_between_discovery_and_inventory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            original = subject.read_repository_files
            calls = []
            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                calls.append(True)
                if len(calls) == 1:
                    path = root / subject.MANIFEST_PATH
                    path.write_bytes(path.read_bytes() + b"\n")
                return result
            with mock.patch.object(subject, "read_repository_files", changed):
                self.assert_category("repository_file_changed", lambda: subject.build_portfolio_snapshot(root))

    def test_new_fixture_during_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            original = subject.profile_machine_event_files
            def changed(*args):
                result = original(*args)
                shutil.copyfile(root / subject.DEFAULT_SAMPLE, root / "data/increments/new.csv")
                return result
            with mock.patch.object(subject, "profile_machine_event_files", changed):
                self.assert_category("fixture_selection_changed", lambda: subject.build_portfolio_snapshot(root))

    def test_profile_input_digest_must_match_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_sources(Path(temporary))
            original = subject.profile_machine_event_files
            def changed(*args):
                result = original(*args)
                result["input"]["files"][0]["sha256"] = "0" * 64
                return result
            with mock.patch.object(subject, "profile_machine_event_files", changed):
                self.assert_category("dataset_profile_source_mismatch", lambda: subject.build_portfolio_snapshot(root))

    def test_fixture_count_and_filename_are_bounded(self):
        for many in (False, True):
            with self.subTest(many=many), tempfile.TemporaryDirectory() as temporary:
                root = copy_sources(Path(temporary))
                if many:
                    for number in range(subject.MAX_FIXTURE_FILES):
                        (root / f"data/increments/extra{number}.csv").write_text("unused")
                    category = "fixture_file_count_exceeded"
                else:
                    (root / "data/increments/`markup`.csv").write_text("unused")
                    category = "fixture_filename_invalid"
                self.assert_category(category, lambda: subject.build_portfolio_snapshot(root))

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            output.mkdir()
            sentinel = output / "existing.txt"
            sentinel.write_text("keep")
            self.assert_category("output_directory_exists", lambda: subject.write_portfolio_snapshot_package(
                subject.build_portfolio_snapshot(ROOT), output))
            self.assertEqual("keep", sentinel.read_text())
            self.assertEqual([sentinel], list(output.iterdir()))

    def test_symlink_output_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "real/nested").mkdir(parents=True)
            os.symlink(root / "real", root / "link")
            self.assert_category("output_parent_symlink", lambda: subject.write_portfolio_snapshot_package(
                subject.build_portfolio_snapshot(ROOT), root / "link/nested/package"))
            self.assertFalse((root / "real/nested/package").exists())

    def test_mutated_snapshot_cannot_be_written_with_stale_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subject.build_portfolio_snapshot(ROOT)
            result["verification"]["databricks_runtime"] = "passed"
            output = Path(temporary) / "package"
            self.assert_category("snapshot_digest_mismatch", lambda: subject.write_portfolio_snapshot_package(result, output))
            self.assertFalse(output.exists())

    def test_cli_success_reports_only_source_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = load_cli().main(["--output-dir", str(Path(temporary) / "package")])
            self.assertEqual(0, code)
            result = json.loads(stdout.getvalue())
            self.assertEqual("created", result["status"])
            self.assertEqual(15, result["reporting_assets"])
            self.assertEqual("repository_source_only", result["evidence_boundary"])
            self.assertEqual("", stderr.getvalue())
            self.assertNotIn(temporary, stdout.getvalue())

    def test_cli_failure_is_sanitized_and_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "package"
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = load_cli().main(["--repository-root", str(Path(temporary) / "private"),
                                        "--output-dir", str(output)])
            self.assertEqual(1, code)
            self.assertEqual("", stdout.getvalue())
            self.assertEqual({"status": "failed", "category": "repository_root_unavailable"}, json.loads(stderr.getvalue()))
            self.assertFalse(output.exists())
            self.assertNotIn(temporary, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
