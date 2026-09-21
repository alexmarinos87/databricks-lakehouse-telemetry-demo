"""Execute the real runtime launcher with isolated standard-library test suites."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = "import unittest\nclass Probe(unittest.TestCase):\n def test_probe(self): pass\n"


class SparkRuntimeRunnerTest(unittest.TestCase):
    def run_suite(self, source=None, filename="test_spark_probe_runtime.py", directory=True, runner_arguments=None, python_options=()):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            for name in ("run_spark_runtime_checks.py", "run_spark_runtime_checks.sh"):
                shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
            if directory:
                (root / "tests_runtime").mkdir()
            if source is not None:
                (root / "tests_runtime" / filename).write_text(source, encoding="utf-8")
            command = (["bash", "scripts/run_spark_runtime_checks.sh"]
                       if runner_arguments is None else
                       ["python3", *python_options, "scripts/run_spark_runtime_checks.py", *runner_arguments])
            return subprocess.run(
                command, cwd=root,
                env={"PATH": os.environ.get("PATH", os.defpath), "HOME": temporary},
                capture_output=True, text=True, timeout=10, check=False,
            )

    def test_passing_runtime_test_exits_zero(self):
        result = self.run_suite(PASS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_original_discovery_arguments_are_parsed_and_executed(self):
        result = self.run_suite(PASS, runner_arguments=[
            "-s", "tests_runtime", "-p", "test_spark_*_runtime.py", "-v",
        ])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("test_probe", result.stderr)

    def test_discovery_overrides_are_rejected_not_ignored(self):
        for arguments in (["-s", "other"], ["-p", "test_other.py"], ["--unknown"]):
            with self.subTest(arguments=arguments):
                result = self.run_suite(PASS, runner_arguments=arguments)
                self.assertEqual(2, result.returncode)
                self.assertNotIn("Ran 1 test", result.stderr)

    def test_default_warning_categories_remain_visible(self):
        for category in ("ResourceWarning", "DeprecationWarning", "ImportWarning"):
            with self.subTest(category=category):
                source = PASS.replace("import unittest", "import unittest, warnings").replace(
                    "pass", f"warnings.warn('synthetic diagnostic', {category})",
                )
                result = self.run_suite(source)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn(f"{category}: synthetic diagnostic", result.stderr)

    def test_explicit_python_warning_options_are_respected(self):
        source = PASS.replace("import unittest", "import unittest, warnings").replace(
            "pass", "warnings.warn('synthetic diagnostic', ResourceWarning)",
        )
        for option, expected in (("error::ResourceWarning", 1), ("ignore::ResourceWarning", 0)):
            with self.subTest(option=option):
                result = self.run_suite(source, runner_arguments=["-v"], python_options=["-W", option])
                self.assertEqual(expected, result.returncode, result.stderr)
                if expected:
                    self.assertIn("ResourceWarning: synthetic diagnostic", result.stderr)
                else:
                    self.assertNotIn("ResourceWarning: synthetic diagnostic", result.stderr)

    def test_empty_directory_is_not_success(self):
        result = self.run_suite()
        self.assertEqual(1, result.returncode)
        self.assertIn("suite is empty", result.stderr)

    def test_nonmatching_test_file_is_not_success(self):
        result = self.run_suite(PASS, filename="test_other.py")
        self.assertEqual(1, result.returncode)
        self.assertIn("suite is empty", result.stderr)

    def test_matching_module_without_tests_is_not_success(self):
        result = self.run_suite("VALUE = 1\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("suite is empty", result.stderr)

    def test_missing_runtime_directory_is_not_success(self):
        result = self.run_suite(directory=False)
        self.assertEqual(1, result.returncode)
        self.assertIn("discovery failed", result.stderr)

    def test_assertion_failure_propagates(self):
        result = self.run_suite(PASS.replace("pass", "self.fail('synthetic failure')"))
        self.assertEqual(1, result.returncode)
        self.assertIn("FAILED (failures=1)", result.stderr)

    def test_runtime_error_propagates(self):
        result = self.run_suite(PASS.replace("pass", "raise RuntimeError('synthetic error')"))
        self.assertEqual(1, result.returncode)
        self.assertIn("FAILED (errors=1)", result.stderr)

    def test_import_failure_propagates(self):
        result = self.run_suite("import deliberately_missing_runtime_dependency\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("FAILED (errors=1)", result.stderr)

    def test_method_skip_is_not_success_even_with_a_passing_test(self):
        result = self.run_suite(PASS + " @unittest.skip('synthetic skip')\n def test_skip(self): pass\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("evidence is incomplete", result.stderr)

    def test_class_setup_skip_is_not_success(self):
        result = self.run_suite(PASS + " @classmethod\n def setUpClass(cls): raise unittest.SkipTest('synthetic skip')\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("evidence is incomplete", result.stderr)

    def test_import_time_skip_is_not_success(self):
        result = self.run_suite("import unittest\nraise unittest.SkipTest('synthetic skip')\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("evidence is incomplete", result.stderr)

    def test_expected_failure_is_not_success(self):
        result = self.run_suite("import unittest\nclass Probe(unittest.TestCase):\n @unittest.expectedFailure\n def test_probe(self): self.fail('synthetic failure')\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("evidence is incomplete", result.stderr)

    def test_unexpected_success_is_not_success(self):
        result = self.run_suite(PASS.replace(" def test_probe", " @unittest.expectedFailure\n def test_probe"))
        self.assertEqual(1, result.returncode)
        self.assertIn("unexpected successes=1", result.stderr)

    def test_subtest_failure_propagates(self):
        result = self.run_suite("import unittest\nclass Probe(unittest.TestCase):\n def test_probe(self):\n  with self.subTest(case=1): self.fail('synthetic subtest')\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("failures=1", result.stderr)

    def custom_suite_source(self, body):
        return (
            "import unittest\n"
            "class Probe(unittest.TestCase):\n"
            " def test_first(self): pass\n"
            " def test_second(self): pass\n"
            "class CustomSuite(unittest.TestSuite):\n"
            " def run(self, result, debug=False):\n"
            + "".join(f"  {line}\n" for line in body.splitlines())
            + "def load_tests(loader, tests, pattern):\n"
            " return CustomSuite([Probe('test_first'), Probe('test_second')])\n"
        )

    def test_discovery_exit_is_not_success(self):
        for code in ("0", "None", "'synthetic exit detail'"):
            with self.subTest(code=code):
                source = f"def load_tests(loader, tests, pattern):\n raise SystemExit({code})\n"
                result = self.run_suite(source)
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("discovery failed", result.stderr)
                self.assertNotIn("synthetic exit detail", result.stderr)

    def test_suite_exit_is_not_success(self):
        for code in ("0", "None", "'synthetic exit detail'"):
            with self.subTest(code=code):
                source = self.custom_suite_source(f"raise SystemExit({code})")
                result = self.run_suite(source)
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("execution exited", result.stderr)
                self.assertNotIn("synthetic exit detail", result.stderr)

    def test_stopped_suite_is_not_success_even_at_full_count(self):
        for execution in ("next(iter(self))(result)", "super().run(result, debug)"):
            with self.subTest(execution=execution):
                source = self.custom_suite_source(execution + "\nresult.stop()\nreturn result")
                result = self.run_suite(source)
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("evidence is incomplete", result.stderr)

    def test_unexecuted_discovered_suite_is_not_success(self):
        result = self.run_suite(self.custom_suite_source("return result"))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("ran 0 of 2 discovered tests", result.stderr)

    def test_partial_suite_without_stop_is_not_success(self):
        result = self.run_suite(self.custom_suite_source(
            "next(iter(self))(result)\nreturn result",
        ))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("ran 1 of 2 discovered tests", result.stderr)

    def test_extra_execution_is_not_complete_discovery_evidence(self):
        result = self.run_suite(self.custom_suite_source(
            "next(iter(self))(result)\nreturn super().run(result, debug)",
        ))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("ran 3 of 2 discovered tests", result.stderr)

    def test_complete_custom_suite_is_success(self):
        result = self.run_suite(self.custom_suite_source("return super().run(result, debug)"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)

    def test_workflow_runs_acceptance_before_spark_without_new_authority(self):
        text = (ROOT / ".github/workflows/spark-runtime.yml").read_text(encoding="utf-8")
        self.assertLess(text.index("Run exact-index acceptance checks"), text.index("Build Spark runtime image"))
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.sha || github.event.before }}", text)
        for expected in ("fetch-depth: 0", "persist-credentials: false", "contents: read",
                         "timeout-minutes: 20", "--cpus=2 --memory=4g", "cancel-in-progress: true"):
            self.assertIn(expected, text)
        for forbidden in ("continue-on-error", "id-token:", "secrets.", "pull_request_target", "schedule:"):
            self.assertNotIn(forbidden, text)
        for path in ("scripts/run_spark_runtime_checks.py", "tests/test_spark_runtime_runner.py",
                     "scripts/run_acceptance_checks.sh"):
            self.assertEqual(2, text.count(f'      - "{path}"'))


if __name__ == "__main__":
    unittest.main()
