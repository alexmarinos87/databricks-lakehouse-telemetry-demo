"""Execute the documented builder command, not a hand-written substitute."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/portfolio_source_quickstart.md"
DOCUMENTED_COMMAND = ["python3", "scripts/build_portfolio_snapshot.py", "--output-dir",
                      ".review/portfolio-snapshot"]


class PortfolioQuickstartTest(unittest.TestCase):
    def command(self, output):
        block = re.search(r"```bash\n([^`]+)```", GUIDE.read_text(encoding="utf-8"))
        self.assertIsNotNone(block)
        arguments = shlex.split(block.group(1))
        # Do not execute arbitrary Markdown shell content or extra commands.
        self.assertEqual(DOCUMENTED_COMMAND, arguments)
        return [sys.executable, *arguments[1:-1], str(output)]

    def run_documented_command(self, output):
        return subprocess.run(
            self.command(output), cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        )

    def test_landing_page_has_the_same_command_within_existing_budget(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(readme.splitlines()), 100)
        self.assertIn(" ".join(DOCUMENTED_COMMAND), readme)
        self.assertIn("(docs/portfolio_source_quickstart.md)", readme)
        self.assertIn("Use a new output directory", readme)
        self.command(Path("unused-test-output"))

    def test_quickstart_links_resolve_within_the_repository(self):
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", GUIDE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(links), 6)
        for target in links:
            with self.subTest(target=target):
                self.assertFalse(target.startswith(("/", "https:", "http:")))
                resolved = (GUIDE.parent / target).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT))
                self.assertTrue(resolved.is_file())

    def test_documented_command_creates_real_source_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "overview"
            result = self.run_documented_command(output)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stderr)
            self.assertEqual("created", json.loads(result.stdout)["status"])
            self.assertEqual({"portfolio-snapshot.json", "portfolio-snapshot.md"},
                             {item.name for item in output.iterdir()})
            snapshot = json.loads((output / "portfolio-snapshot.json").read_text())
            self.assertEqual(31, snapshot["dataset_profile"]["rows"]["physical_row_count"])
            self.assertEqual(30, snapshot["dataset_profile"]["rows"]["unique_event_id_count"])
            self.assertEqual(1, snapshot["dataset_profile"]["rows"]["replay_duplicate_row_count"])
            self.assertEqual(15, snapshot["reporting"]["asset_count"])
            self.assertEqual("not_run", snapshot["verification"]["databricks_runtime"])
            self.assertIs(False, snapshot["verification"]["deployment_authorized"])

    def test_documented_rerun_preserves_the_original_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "overview"
            first = self.run_documented_command(output)
            self.assertEqual(0, first.returncode, first.stderr)
            before = {item.name: item.read_bytes() for item in output.iterdir()}
            second = self.run_documented_command(output)
            self.assertEqual(1, second.returncode)
            self.assertEqual("", second.stdout)
            self.assertEqual({"status": "failed", "category": "output_directory_exists"},
                             json.loads(second.stderr))
            self.assertEqual(before, {item.name: item.read_bytes() for item in output.iterdir()})


if __name__ == "__main__":
    unittest.main()
