"""Profiler CLI failure contracts and real subprocess integration regressions."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_cli():
    spec = importlib.util.spec_from_file_location(
        "profile_cli_errors", ROOT / "scripts/profile_synthetic_dataset.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProfileCLIErrorTest(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.args = ["--repository-root", "source", "--output-dir", "output"]
        self.profile = {
            "rows": {"physical_row_count": 2, "unique_event_id_count": 1},
            "coverage": {"machine_count": 1},
            "evidence_boundary": "repository_source_only",
        }
        for name, result in (
            ("default_machine_event_sources", ("data/sample_machine_events.csv",)),
            ("profile_machine_event_files", self.profile),
            ("write_dataset_profile_package", Path("output")),
        ):
            patcher = mock.patch.object(self.cli, name, return_value=result)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def run_cli(self, args=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(self.args if args is None else args)
        return code, out.getvalue(), err.getvalue()

    def test_success_preserves_existing_json_contract(self):
        code, out, err = self.run_cli()
        self.assertEqual(0, code)
        self.assertEqual("", err)
        self.assertEqual({"output_dir": "output", "physical_rows": 2, "unique_event_ids": 1,
                          "machines": 1, "evidence_boundary": "repository_source_only"}, json.loads(out))
        self.default_machine_event_sources.assert_called_once_with("source")
        self.write_dataset_profile_package.assert_called_once_with(self.profile, "output")

    def test_explicit_sources_bypass_default_discovery(self):
        code, _, _ = self.run_cli(self.args + ["--source", "one.csv", "--source", "two.csv"])
        self.assertEqual(0, code)
        self.default_machine_event_sources.assert_not_called()
        self.profile_machine_event_files.assert_called_once_with("source", ["one.csv", "two.csv"])

    def test_expected_errors_at_each_stage_have_one_stable_stderr_record(self):
        for stage in ("default_machine_event_sources", "profile_machine_event_files",
                      "write_dataset_profile_package"):
            with self.subTest(stage=stage):
                operation = getattr(self, stage)
                operation.side_effect = self.cli.DatasetProfileError("repository_file_unavailable")
                code, out, err = self.run_cli()
                operation.side_effect = None
                self.assertEqual(1, code)
                self.assertEqual("", out)
                self.assertEqual(1, len(err.splitlines()))
                self.assertEqual({"status": "failed", "category": "repository_file_unavailable",
                                  "details": []}, json.loads(err))

    def test_chained_provider_diagnostic_is_not_printed(self):
        try:
            raise OSError("PRIVATE_PATH_AND_PROVIDER_MARKER")
        except OSError as cause:
            error = self.cli.DatasetProfileError("machine_event_validation_failed", ["header_mismatch"])
            error.__cause__ = cause
        self.profile_machine_event_files.side_effect = error
        code, out, err = self.run_cli()
        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertEqual({"status": "failed", "category": "machine_event_validation_failed",
                          "details": ["header_mismatch"]}, json.loads(err))
        self.assertNotIn("PRIVATE", err)
        self.assertNotIn("Traceback", err)
        self.write_dataset_profile_package.assert_not_called()

    def test_discovery_oserror_is_sanitized_before_profiling(self):
        self.default_machine_event_sources.side_effect = PermissionError("PRIVATE_DISCOVERY_PATH")
        code, out, err = self.run_cli()
        self.assertEqual((1, ""), (code, out))
        self.assertEqual({"status": "failed", "category": "dataset_profile_io_failed", "details": []},
                         json.loads(err))
        self.profile_machine_event_files.assert_not_called()
        self.write_dataset_profile_package.assert_not_called()

    def test_invalid_path_value_is_sanitized(self):
        self.default_machine_event_sources.side_effect = ValueError("PRIVATE_PATH_VALUE")
        code, out, err = self.run_cli()
        self.assertEqual((1, ""), (code, out))
        self.assertEqual("dataset_profile_io_failed", json.loads(err)["category"])
        self.assertNotIn("PRIVATE", err)

    def test_unexpected_programming_errors_are_not_relabelled(self):
        self.profile_machine_event_files.side_effect = RuntimeError("programming defect")
        with self.assertRaisesRegex(RuntimeError, "programming defect"):
            self.run_cli()
        self.write_dataset_profile_package.assert_not_called()

    def test_process_interrupt_is_not_swallowed(self):
        self.profile_machine_event_files.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli()
        self.write_dataset_profile_package.assert_not_called()

    def test_no_argument_call_retains_sys_argv_support(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["profile", *self.args]), contextlib.redirect_stdout(out):
            self.assertEqual(0, self.cli.main())
        self.assertEqual(2, json.loads(out.getvalue())["physical_rows"])


class ProfileCLISubprocessTest(unittest.TestCase):
    def invoke(self, root, output, source=None):
        command = [sys.executable, str(ROOT / "scripts/profile_synthetic_dataset.py"),
                   "--repository-root", str(root), "--output-dir", str(output)]
        if source is not None:
            command += ["--source", source]
        return subprocess.run(command, capture_output=True, text=True, timeout=20)

    def assert_failure(self, result, category, private_marker):
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(1, len(result.stderr.splitlines()))
        self.assertEqual(category, json.loads(result.stderr)["category"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(private_marker, result.stderr)

    def test_missing_repository_uses_json_not_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "PRIVATE_MISSING_ROOT"
            output = Path(temporary) / "output"
            self.assert_failure(self.invoke(root, output), "repository_root_unavailable", temporary)
            self.assertFalse(output.exists())

    def test_invalid_header_does_not_echo_input_or_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "events.csv").write_text("PRIVATE_HEADER_MARKER\n", encoding="utf-8")
            output = root / "output"
            self.assert_failure(self.invoke(root, output, "events.csv"),
                                "machine_event_validation_failed", "PRIVATE_HEADER_MARKER")
            self.assertFalse(output.exists())

    def test_valid_package_and_existing_output_preservation(self):
        from test_dataset_profile import make_row, write_csv
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = make_row("E1", "2026-04-01T06:00:00Z", cost="2.50")
            write_csv(root / "events.csv", [row, row])
            output = root / "output"
            result = self.invoke(root, output, "events.csv")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stderr)
            self.assertEqual(2, json.loads(result.stdout)["physical_rows"])
            self.assertEqual(1, json.loads(result.stdout)["unique_event_ids"])
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            self.assertEqual({"dataset-profile.json", "dataset-profile.md"}, set(before))
            again = self.invoke(root, output, "events.csv")
            self.assert_failure(again, "output_directory_exists", temporary)
            self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_profile_number_budget_rejection_is_sanitized(self):
        from test_dataset_profile import make_row, write_csv
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(root / "events.csv", [make_row("E1", "2026-04-01T06:00:00Z", cost="1e1001")])
            output = root / "output"
            self.assert_failure(self.invoke(root, output, "events.csv"),
                                "machine_event_profile_cost_limit_exceeded", temporary)
            self.assertFalse(output.exists())

    def test_symlink_source_is_rejected_without_raw_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "real.csv").write_text("unused\n", encoding="utf-8")
            (root / "linked.csv").symlink_to(root / "real.csv")
            output = root / "output"
            self.assert_failure(self.invoke(root, output, "linked.csv"),
                                "repository_file_symlink", temporary)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
