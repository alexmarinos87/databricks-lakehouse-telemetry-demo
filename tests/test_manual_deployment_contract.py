import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment.md"


class ManualDeploymentContractTest(unittest.TestCase):
    def test_databricks_workflow_is_manual_only(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        trigger = workflow.split("\npermissions:\n", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("\n  push:\n", trigger)
        self.assertNotIn("github.event_name == 'push'", workflow)

    def test_plan_is_default_and_apply_requires_explicit_input(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("apply_changes:", workflow)
        self.assertIn("Leave disabled for validate-and-plan only", workflow)
        self.assertGreaterEqual(workflow.count("default: false"), 3)
        self.assertIn(
            "github.event.inputs.target == 'dev' && "
            "github.event.inputs.apply_changes == 'true'",
            workflow,
        )
        self.assertIn(
            "github.event.inputs.target == 'prod' && "
            "github.event.inputs.apply_changes == 'true'",
            workflow,
        )

    def test_optional_runtime_side_effects_default_off(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("upload_sample_data:", workflow)
        self.assertIn("run_workflow:", workflow)
        self.assertEqual(
            2,
            workflow.count("if: github.event.inputs.upload_sample_data == 'true'"),
        )
        self.assertEqual(
            4,
            workflow.count("if: github.event.inputs.run_workflow == 'true'"),
        )

    def test_documentation_requires_plan_review_before_apply(self):
        documentation = DEPLOYMENT_DOC.read_text(encoding="utf-8")

        self.assertIn("A merge to `main` never deploys", documentation)
        self.assertIn("Leave `apply_changes` disabled", documentation)
        self.assertIn("Review the completed `bundle validate` and `bundle plan`", documentation)
        self.assertIn("does not cryptographically bind", documentation)


if __name__ == "__main__":
    unittest.main()
