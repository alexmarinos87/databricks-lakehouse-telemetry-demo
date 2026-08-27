from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "docs" / "evidence_workflow_quickstart.md"


class EvidenceWorkflowQuickstartTest(unittest.TestCase):
    def test_quickstart_orders_the_evidence_gates_and_resolves_commands(self):
        text = QUICKSTART.read_text(encoding="utf-8")
        headings = [
            "## 1. Confirm external readiness",
            "## 2. Verify effective external controls",
            "## 3. Index one coherent control state",
            "## 4. Capture and review a plan-only run",
            "## 5. Package controlled development-runtime evidence",
            "## 6. Verify operational evidence separately",
            "## Stop conditions",
        ]
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(sorted(positions), positions)

        script_paths = sorted(set(re.findall(r"scripts/[a-z0-9_]+\.py", text)))
        self.assertEqual(
            [
                "scripts/build_alert_delivery_evidence.py",
                "scripts/build_development_runtime_evidence.py",
                "scripts/build_external_control_evidence_index.py",
                "scripts/check_external_readiness.py",
                "scripts/plan_history_retention.py",
                "scripts/verify_databricks_federation.py",
                "scripts/verify_github_governance.py",
                "scripts/verify_identity_privilege_evidence.py",
            ],
            script_paths,
        )
        for relative_path in script_paths:
            with self.subTest(path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())

    def test_quickstart_preserves_plan_only_and_no_automatic_action_boundaries(self):
        text = QUICKSTART.read_text(encoding="utf-8")
        for required in (
            "/databricks-plan dev",
            "plan-only",
            "external_mutation_authorized: false",
            "A blocked result must remain blocked",
            "dry-run-only",
            "must not delete, vacuum or mutate history",
            "No command on this page authorises production activity",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

        for forbidden in (
            "DATABRICKS_CLIENT_SECRET=",
            "databricks bundle deploy",
            "VACUUM ",
            "DROP TABLE",
            "--apply",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
