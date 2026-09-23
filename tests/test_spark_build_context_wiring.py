"""Keep Docker's context selectors in both Spark workflow path filters.

These inspect this repository's block-style workflow, not GitHub's event engine.
"""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/spark-runtime.yml"


def event_paths(text: str, event: str) -> list[str]:
    """Read the quoted path entries in one known workflow event block."""
    start = text.index(f"  {event}:\n")
    block = text[start:].splitlines()[1:]
    paths = []
    in_paths = False
    for line in block:
        if line.strip() and not line.startswith("    "):
            break
        if line == "    paths:":
            in_paths = True
        elif in_paths and line.startswith("    ") and not line.startswith("      "):
            break
        elif in_paths and line.strip():
            match = re.fullmatch(r'      - "([^"\n]+)"', line)
            if match is None:
                raise AssertionError("Review the changed workflow path-list syntax")
            paths.append(match.group(1))
    return paths


class SparkBuildContextWiringTest(unittest.TestCase):
    def assert_context_filters(self, event: str) -> None:
        paths = event_paths(WORKFLOW.read_text(encoding="utf-8"), event)
        for path in (".dockerignore", "Dockerfile.spark-ci.dockerignore"):
            with self.subTest(event=event, path=path):
                self.assertEqual(1, paths.count(path), f"Missing/duplicate {event} path: {path}")

    def test_pull_request_watches_both_docker_ignore_files(self):
        self.assert_context_filters("pull_request")

    def test_main_push_watches_both_docker_ignore_files(self):
        self.assert_context_filters("push")
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  push:\n    branches:\n      - main\n", text)

    def test_context_paths_match_the_actual_spark_build(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        commands = re.findall(r"^        run: (docker build .+)$", text, flags=re.MULTILINE)
        self.assertEqual(1, len(commands))
        command = shlex.split(commands[0])
        self.assertEqual(".", command[-1])
        self.assertEqual("Dockerfile.spark-ci", command[command.index("-f") + 1])
        self.assertTrue((ROOT / "Dockerfile.spark-ci").is_file())


if __name__ == "__main__":
    unittest.main()
