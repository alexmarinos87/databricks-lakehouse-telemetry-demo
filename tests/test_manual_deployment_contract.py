import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment.md"
PLAN_SCRIPT = REPO_ROOT / "scripts" / "capture_databricks_plan.py"

UPLOAD_ARTIFACT_SHA = (
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
)


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
        self.assertIn("needs: diff-dev", workflow)
        self.assertIn("needs: diff-prod", workflow)
        self.assertIn("environment: dev-plan", workflow)
        self.assertIn("environment: prod-plan", workflow)
        self.assertEqual(4, workflow.count("github.ref == 'refs/heads/main'"))
        self.assertIn("Require protected main ref", workflow)

    def test_optional_runtime_side_effects_default_off_and_governed(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("upload_sample_data:", workflow)
        self.assertIn("run_workflow:", workflow)
        self.assertIn("demo_dataset:", workflow)
        self.assertIn("ingestion_mode:", workflow)
        self.assertIn("backfill_id:", workflow)
        self.assertEqual(
            6,
            workflow.count("if: github.event.inputs.upload_sample_data == 'true'"),
        )
        self.assertEqual(
            4,
            workflow.count("if: github.event.inputs.run_workflow == 'true'"),
        )
        self.assertEqual(2, workflow.count("scripts/plan_ingestion_upload.py"))
        self.assertEqual(2, workflow.count("scripts/upload_ingestion_plan.py"))
        self.assertNotIn("--overwrite", workflow)
        self.assertNotIn("databricks fs rm", workflow)

    def test_databricks_jobs_use_short_lived_github_oidc(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(4, workflow.count("id-token: write"))
        self.assertEqual(4, workflow.count("DATABRICKS_AUTH_TYPE: github-oidc"))
        self.assertEqual(8, workflow.count("DATABRICKS_CLIENT_ID:"))
        self.assertEqual(4, workflow.count("DATABRICKS_HOST:"))
        self.assertNotIn("DATABRICKS_CLIENT_SECRET", workflow)
        self.assertEqual(
            6, workflow.count("python3 scripts/capture_databricks_plan.py")
        )
        self.assertEqual(2, workflow.count("--mode plan"))
        self.assertEqual(4, workflow.count("--mode identity"))

    def test_plan_and_identity_evidence_are_retained_by_exact_commit(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(6, workflow.count(UPLOAD_ARTIFACT_SHA))
        self.assertEqual(6, workflow.count("retention-days: 14"))
        self.assertEqual(6, workflow.count("if-no-files-found: error"))
        self.assertIn(
            "databricks-dev-plan-${{ github.sha }}-${{ github.run_attempt }}",
            workflow,
        )
        self.assertIn(
            "databricks-prod-plan-${{ github.sha }}-${{ github.run_attempt }}",
            workflow,
        )
        self.assertIn("Publish dev plan evidence summary", workflow)
        self.assertIn("Publish prod plan evidence summary", workflow)
        self.assertIn("output/databricks-plan/dev", workflow)
        self.assertIn("output/databricks-plan/prod", workflow)

    def test_plan_capture_is_bounded_and_rejects_static_secrets(self):
        script = PLAN_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"github-oidc"', script)
        self.assertIn('"DATABRICKS_CLIENT_SECRET"', script)
        self.assertIn('"static_client_secret_is_present"', script)
        self.assertIn('"current-user", "me"', script)
        self.assertIn("DEFAULT_IDENTITY_TIMEOUT_SECONDS", script)
        self.assertIn("DEFAULT_VALIDATE_TIMEOUT_SECONDS", script)
        self.assertIn("DEFAULT_PLAN_TIMEOUT_SECONDS", script)
        self.assertIn("MAX_CAPTURE_BYTES", script)
        self.assertIn("timeout=timeout_seconds", script)
        self.assertIn("configured_client_id_fingerprint", script)
        self.assertNotIn("check_output", script)
        self.assertNotIn("check_call", script)

    def test_documentation_requires_federation_plan_review_and_approval(self):
        documentation = DEPLOYMENT_DOC.read_text(encoding="utf-8")

        self.assertIn("A merge to `main` never deploys", documentation)
        self.assertIn("Leave `apply_changes` disabled", documentation)
        self.assertIn(
            "Review the completed `bundle validate` and `bundle plan`",
            documentation,
        )
        self.assertIn("DATABRICKS_AUTH_TYPE=github-oidc", documentation)
        self.assertIn("id-token: write", documentation)
        self.assertIn(
            "repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:dev",
            documentation,
        )
        self.assertIn("does not cryptographically bind", documentation)
        self.assertIn("environment approval", documentation)
        self.assertIn("reuse the established Auto Loader checkpoint", documentation)
        self.assertIn("does not use a fixed landing filename", documentation)


if __name__ == "__main__":
    unittest.main()
