from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROHIBITED_EXACT_ROOT_FILES = {"nonexistent"}
INCIDENT_ROOT_FILES = {
    "__do_not_create__",
    "__invalid__",
    "__invalid_dispatch_probe__",
    "__invalid_probe__",
    "__never__",
    "__probe__",
    "__probe_never_exists__",
    "__this_path_should_not_exist__",
    "__workflow_dispatch_probe__",
    "nonexistent",
}


def is_prohibited_root_file(name: str) -> bool:
    """Identify scratch/sentinel files that must never be committed at repo root."""

    return name in PROHIBITED_EXACT_ROOT_FILES or (
        name.startswith("__") and name.endswith("__")
    )


class RepositoryRootHygieneTest(unittest.TestCase):
    def test_repository_root_contains_no_probe_or_sentinel_files(self):
        offenders = sorted(
            candidate.name
            for candidate in ROOT.iterdir()
            if candidate.is_file() and is_prohibited_root_file(candidate.name)
        )
        self.assertEqual(
            [],
            offenders,
            "remove root-level scratch/probe artifacts before accepting the candidate",
        )

    def test_policy_covers_every_file_from_the_probe_artifact_incident(self):
        uncovered = sorted(
            name for name in INCIDENT_ROOT_FILES if not is_prohibited_root_file(name)
        )
        self.assertEqual([], uncovered)

    def test_normal_project_root_files_are_not_rejected(self):
        for name in (
            "AGENTS.md",
            "Dockerfile.ci",
            "Dockerfile.spark-ci",
            "PROJECT_REFERENCE.md",
            "README.md",
            "databricks.yml",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_prohibited_root_file(name))


if __name__ == "__main__":
    unittest.main()
