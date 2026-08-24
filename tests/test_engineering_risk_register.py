from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = REPO_ROOT / "governance" / "engineering_risk_register.json"
MARKDOWN_PATH = REPO_ROOT / "docs" / "engineering_risk_register.md"
RENDERER_PATH = REPO_ROOT / "scripts" / "render_engineering_risk_register.py"

SPEC = importlib.util.spec_from_file_location(
    "render_engineering_risk_register",
    RENDERER_PATH,
)
assert SPEC is not None and SPEC.loader is not None
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)

TOP_LEVEL_KEYS = {
    "schema_version",
    "as_of_date",
    "repository_evidence_boundary",
    "risks",
}
RISK_KEYS = {
    "id",
    "title",
    "priority",
    "owner",
    "lifecycle",
    "source_status",
    "runtime_status",
    "source_evidence",
    "external_dependencies",
    "residual_risk",
    "next_evidence",
}
ALLOWED_PRIORITIES = {"critical", "high", "medium"}
ALLOWED_SOURCE_STATUSES = {
    "source_mitigated",
    "source_gap_open",
    "not_source_controlled",
}
ALLOWED_RUNTIME_STATUSES = {
    "runtime_evidence_pending",
    "externally_blocked",
    "not_applicable",
}
EXTERNAL_DEPENDENCY_PATTERN = re.compile(
    r"(?:issue:\d+|github:[a-z0-9-]+|databricks:[a-z0-9-]+|external:[a-z0-9-]+)\Z"
)


class EngineeringRiskRegisterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.register = json.loads(REGISTER_PATH.read_text(encoding="utf-8"))
        cls.markdown = MARKDOWN_PATH.read_text(encoding="utf-8")
        cls.risks = cls.register["risks"]
        cls.by_id = {risk["id"]: risk for risk in cls.risks}

    def test_register_shape_ids_and_status_values_are_strict(self):
        self.assertEqual(TOP_LEVEL_KEYS, set(self.register))
        self.assertEqual(1, self.register["schema_version"])
        self.assertRegex(self.register["as_of_date"], r"\A\d{4}-\d{2}-\d{2}\Z")
        self.assertTrue(self.register["repository_evidence_boundary"].strip())

        expected_ids = [f"R-{number:03d}" for number in range(1, 18)]
        self.assertEqual(expected_ids, [risk["id"] for risk in self.risks])
        self.assertEqual(len(self.risks), len(self.by_id))

        for risk in self.risks:
            with self.subTest(risk=risk["id"]):
                self.assertEqual(RISK_KEYS, set(risk))
                self.assertIn(risk["priority"], ALLOWED_PRIORITIES)
                self.assertEqual("repository_maintainer", risk["owner"])
                self.assertEqual("open", risk["lifecycle"])
                self.assertIn(risk["source_status"], ALLOWED_SOURCE_STATUSES)
                self.assertIn(risk["runtime_status"], ALLOWED_RUNTIME_STATUSES)
                self.assertTrue(risk["title"].strip())
                self.assertTrue(risk["residual_risk"].strip())
                self.assertTrue(risk["next_evidence"].strip())

    def test_source_evidence_is_current_repo_content_not_delivery_history(self):
        serialized = REGISTER_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(serialized, r"\bPR #\d+")
        self.assertNotIn("/pull/", serialized)
        self.assertNotIn("agent/", serialized)
        self.assertNotRegex(serialized, r"\b[0-9a-f]{40}\b")

        for risk in self.risks:
            evidence_paths = risk["source_evidence"]
            with self.subTest(risk=risk["id"]):
                self.assertIsInstance(evidence_paths, list)
                self.assertTrue(evidence_paths)
                self.assertEqual(len(evidence_paths), len(set(evidence_paths)))
                for raw_path in evidence_paths:
                    path = Path(raw_path)
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    resolved = (REPO_ROOT / path).resolve()
                    self.assertTrue(resolved.is_relative_to(REPO_ROOT))
                    self.assertTrue(
                        resolved.is_file(),
                        f"{risk['id']} evidence does not exist: {raw_path}",
                    )

    def test_source_mitigation_never_implies_runtime_closure(self):
        for risk in self.risks:
            with self.subTest(risk=risk["id"]):
                if risk["source_status"] == "source_mitigated":
                    self.assertIn(
                        risk["runtime_status"],
                        {"runtime_evidence_pending", "externally_blocked"},
                    )
                if risk["runtime_status"] == "externally_blocked":
                    self.assertTrue(risk["external_dependencies"])
                for dependency in risk["external_dependencies"]:
                    self.assertRegex(dependency, EXTERNAL_DEPENDENCY_PATTERN)

        self.assertEqual(
            "source_mitigated",
            self.by_id["R-011"]["source_status"],
        )
        self.assertEqual(
            "runtime_evidence_pending",
            self.by_id["R-011"]["runtime_status"],
        )
        self.assertEqual(
            "not_source_controlled",
            self.by_id["R-007"]["source_status"],
        )
        self.assertEqual(
            {"R-001", "R-007", "R-015", "R-016"},
            {
                risk["id"]
                for risk in self.risks
                if risk["runtime_status"] == "externally_blocked"
            },
        )

    def test_major_controls_map_to_current_evidence(self):
        required_evidence = {
            "R-001": {
                ".github/workflows/deploy.yml",
                "scripts/capture_databricks_plan.py",
            },
            "R-005": {
                "governance/warehouse_assignment_policy.json",
                "src/lakehouse_demo/spark_assignment_history.py",
            },
            "R-006": {
                "governance/downtime_semantics.json",
                "src/lakehouse_demo/spark_downtime_semantics.py",
            },
            "R-009": {
                "notebooks/02_silver_transform.py",
                "notebooks/03_gold_models.py",
                "notebooks/05_forecast_validation.py",
                "notebooks/07_warehouse_model.py",
            },
            "R-010": {
                "src/lakehouse_demo/ingestion_identity.py",
                "scripts/upload_ingestion_plan.py",
            },
            "R-011": {
                "governance/reporting_query_policy.json",
                "scripts/upsert_reporting_queries.py",
                "tests/test_reporting_query_policy.py",
                "docs/reporting_query_ownership.md",
            },
            "R-015": {
                "governance/runtime_identity_policy.json",
                "config/identity_privilege_contract.json",
            },
            "R-016": {
                "governance/operational_alert_policy.json",
                "sql/operational_health.sql",
            },
            "R-017": {
                "governance/runtime_compatibility.json",
                "requirements-spark.txt",
            },
        }
        for risk_id, required_paths in required_evidence.items():
            with self.subTest(risk=risk_id):
                self.assertTrue(
                    required_paths.issubset(set(self.by_id[risk_id]["source_evidence"]))
                )

    def test_markdown_is_deterministically_rendered_from_json(self):
        self.assertEqual(
            renderer.render_register(self.register),
            self.markdown,
        )
        for risk in self.risks:
            summary_row = (
                f"| {risk['id']} | {renderer.PRIORITY_LABELS[risk['priority']]} | "
                f"{renderer.SOURCE_STATUS_LABELS[risk['source_status']]} | "
                f"{renderer.RUNTIME_STATUS_LABELS[risk['runtime_status']]} | "
                f"{risk['title']} |"
            )
            self.assertIn(summary_row, self.markdown)

        self.assertIn("## Status model", self.markdown)
        self.assertIn("## Current risks", self.markdown)
        self.assertIn("## External blockers", self.markdown)
        self.assertIn("## Closure rule", self.markdown)
        self.assertIn("Source mitigated", self.markdown)
        self.assertIn("Runtime evidence pending", self.markdown)
        self.assertIn("Externally blocked", self.markdown)
        self.assertNotRegex(self.markdown, r"\bPR #\d+")
        self.assertNotIn("agent/", self.markdown)

    def test_open_source_and_external_goals_are_not_hidden(self):
        reporting_risk = self.by_id["R-011"]
        self.assertNotIn("G12", reporting_risk["next_evidence"])
        self.assertIn(
            "governance/reporting_query_policy.json",
            reporting_risk["source_evidence"],
        )
        self.assertIn("effective", reporting_risk["residual_risk"].lower())
        self.assertIn("development", reporting_risk["next_evidence"].lower())
        self.assertIn("issue:44", self.by_id["R-001"]["external_dependencies"])
        self.assertIn("issue:44", self.by_id["R-007"]["external_dependencies"])
        self.assertIn("issue:44", self.by_id["R-015"]["external_dependencies"])
        self.assertIn(
            "external:notification-destination",
            self.by_id["R-016"]["external_dependencies"],
        )


if __name__ == "__main__":
    unittest.main()
