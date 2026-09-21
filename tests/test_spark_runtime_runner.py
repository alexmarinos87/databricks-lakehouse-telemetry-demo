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
    def run_suite(self, source=None, filename="test_spark_probe_runtime.py", directory=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            for name in ("run_spark_runtime_checks.py", "run_spark_runtime_checks.sh"):
                shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
            if directory:
                (root / "tests_runtime").mkdir()
            if source is not None:
                (root / "tests_runtime" / filename).write_text(source, encoding="utf-8")
            return subprocess.run(
                ["bash", "scripts/run_spark_runtime_checks.sh"], cwd=root,
                env={"PATH": os.environ.get("PATH", os.defpath), "HOME": temporary},
                capture_output=True, text=True, timeout=10, check=False,
            )

    def test_passing_runtime_test_exits_zero(self):
        result = self.run_suite(PASS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

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
