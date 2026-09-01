from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
REFERENCE = ROOT / "PROJECT_REFERENCE.md"


class PortfolioReadmeTest(unittest.TestCase):
    def test_landing_page_is_concise_and_covers_the_engineering_case_study(self):
        text = README.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 100)
        for required in (
            "# Databricks Lakehouse Telemetry Demo",
            "## At a glance",
            "## Architecture",
            "## What makes this more than a notebook demo",
            "## Validate locally",
            "## Explore the evidence",
            "## Data and evidence boundary",
            "Immutable, content-addressed files",
            "versioned publication manifests",
            "dimensional warehouse",
            "Reporting asset catalogue",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_all_local_landing_page_links_resolve_inside_the_repository(self):
        text = README.read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        self.assertGreaterEqual(len(links), 10)

        for target in links:
            with self.subTest(target=target):
                self.assertNotIn("\\", target)
                self.assertFalse(target.startswith(("/", "http://", "https://")))
                path = Path(target)
                self.assertNotIn("..", path.parts)
                self.assertTrue((ROOT / path).exists())

    def test_complete_previous_walkthrough_is_preserved_separately(self):
        self.assertTrue(REFERENCE.is_file())
        text = REFERENCE.read_text(encoding="utf-8")
        for required in (
            "# Databricks Lakehouse Demo",
            "## Project Structure",
            "## How To Run In Databricks",
            "## Workflow Job",
            "## Local Validation",
            "## Interview Summary",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertGreater(len(text.splitlines()), 200)

    def test_landing_page_preserves_runtime_and_human_authority_boundaries(self):
        text = README.read_text(encoding="utf-8")
        for required in (
            "Automation produces evidence; human review remains the merge and deployment authority.",
            "does not claim that a production workspace or client system has been operated",
            "Source-controlled gates and green CI do not prove effective branch protection",
            "live OIDC federation",
            "Databricks runtime deployment",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

        lowered = text.lower()
        for forbidden in (
            "production-ready",
            "running in production",
            "deployed to production",
            "live client data",
            "fully operational",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_documented_validation_and_reporting_entrypoints_exist(self):
        for relative_path in (
            "scripts/run_local_checks.sh",
            "scripts/run_spark_runtime_checks.sh",
            "sql/reporting_assets/manifest.json",
            "docs/evidence_workflow_quickstart.md",
            "docs/engineering_risk_register.md",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).exists())


if __name__ == "__main__":
    unittest.main()
