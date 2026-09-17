"""Portable contracts for when the actual committed-fixture Spark tests run."""

import fnmatch
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/spark-runtime.yml"
DEPENDENCIES = (
    "data/sample_machine_events.csv",
    "data/increments/machine_events_increment_2026_04_03.csv",
    "src/lakehouse_demo/azure_ingestion.py",
    "src/lakehouse_demo/dataset_profile.py",
    "src/lakehouse_demo/downtime_pipeline.py",
    "src/lakehouse_demo/fixture_discovery.py",
    "src/lakehouse_demo/machine_event_contract.py",
    "src/lakehouse_demo/repository_files.py",
    "tests_runtime/test_spark_fixture_reconciliation_runtime.py",
)


class FixtureRuntimeWiringTest(unittest.TestCase):
    def test_both_events_watch_the_fixture_and_reconciliation_dependencies(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        pull_request, push = text.split("  push:", 1)
        push = push.split("\npermissions:", 1)[0]
        patterns = []
        for section in (pull_request, push):
            selected = re.findall(r'^      - "([^"\n]+)"$', section, re.MULTILINE)
            patterns.append(selected)
            for path in DEPENDENCIES:
                with self.subTest(path=path):
                    self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for pattern in selected))
        self.assertEqual(patterns[0], patterns[1])

    def test_runtime_remains_bounded_and_has_no_workspace_authority(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for value in ("contents: read", "persist-credentials: false", "timeout-minutes: 20",
                      "cancel-in-progress: true", "--cpus=2 --memory=4g", "runs-on: ubuntu-24.04"):
            self.assertIn(value, text)
        for forbidden in ("id-token:", "secrets.", "environment:", "pull_request_target",
                          "workflow_dispatch:", "schedule:", "continue-on-error", "contents: write"):
            self.assertNotIn(forbidden, text)
        for line in text.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[0-9a-f]{40}$")

    def test_new_runtime_file_is_in_the_existing_discovery_pattern(self):
        path = ROOT / "tests_runtime/test_spark_fixture_reconciliation_runtime.py"
        self.assertTrue(path.is_file())
        script = (ROOT / "scripts/run_spark_runtime_checks.sh").read_text(encoding="utf-8")
        self.assertIn("-s tests_runtime", script)
        self.assertIn("test_spark_*_runtime.py", script)
        self.assertTrue(fnmatch.fnmatchcase(path.name, "test_spark_*_runtime.py"))


if __name__ == "__main__":
    unittest.main()
