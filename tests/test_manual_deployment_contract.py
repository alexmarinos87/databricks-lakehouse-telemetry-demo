import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment.md"
APPLY_REVIEW_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "require_plan_review_before_apply.md"
)
PLAN_SCRIPT = REPO_ROOT / "scripts" / "capture_databricks_plan.py"
PLAN_CORE = REPO_ROOT / "scripts" / "plan_evidence" / "core.py"
PLAN_CAPTURE = REPO_ROOT / "scripts" / "plan_evidence" / "capture.py"
PLAN_VERIFIER = REPO_ROOT / "scripts" / "verify_bundle_plan_artifact.py"
BUNDLE = REPO_ROOT / "databricks.yml"

UPLOAD_ARTIFACT_SHA = (
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
)
DOWNLOAD_ARTIFACT_SHA = (
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
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
        self.assertEqual(2, workflow.count("actions: read"))
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
        self.assertEqual(2, workflow.count(DOWNLOAD_ARTIFACT_SHA))
        self.assertEqual(2, workflow.count("digest-mismatch: error"))
        self.assertEqual(6, workflow.count("retention-days: 14"))
        self.assertEqual(6, workflow.count("if-no-files-found: error"))
        self.assertEqual(
            2,
            workflow.count(
                "databricks-dev-plan-${{ github.sha }}-${{ github.run_attempt }}"
            ),
        )
        self.assertEqual(
            2,
            workflow.count(
                "databricks-prod-plan-${{ github.sha }}-${{ github.run_attempt }}"
            ),
        )
        self.assertIn("Publish dev plan evidence summary", workflow)
        self.assertIn("Publish prod plan evidence summary", workflow)
        self.assertIn("output/databricks-plan/dev", workflow)
        self.assertIn("output/databricks-plan/prod", workflow)
        self.assertIn("input/databricks-plan/dev", workflow)
        self.assertIn("input/databricks-plan/prod", workflow)

    def test_apply_downloads_verifies_and_replays_same_run_plan(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(2, workflow.count("scripts/verify_bundle_plan_artifact.py"))
        self.assertEqual(2, workflow.count("--expected-repository \"${GITHUB_REPOSITORY}\""))
        self.assertEqual(2, workflow.count("--expected-ref \"${GITHUB_REF}\""))
        self.assertEqual(2, workflow.count("--expected-commit \"${GITHUB_SHA}\""))
        self.assertEqual(2, workflow.count("--expected-run-id \"${GITHUB_RUN_ID}\""))
        self.assertEqual(
            2,
            workflow.count(
                "--expected-run-attempt \"${GITHUB_RUN_ATTEMPT}\""
            ),
        )
        self.assertEqual(
            2,
            workflow.count('--plan "${PLAN_INPUT_DIR}/bundle-plan.json"'),
        )

        dev_apply = workflow.split("  deploy-dev:", 1)[1].split("  diff-prod:", 1)[0]
        prod_apply = workflow.split("  deploy-prod:", 1)[1]
        for target, section in (("dev", dev_apply), ("prod", prod_apply)):
            with self.subTest(target=target):
                download = section.index(f"Download reviewed {target} plan evidence")
                verify = section.index(f"Verify reviewed {target} plan artifact")
                cli = section.index("Install Databricks CLI")
                deploy = section.index(f"Deploy {target} bundle from reviewed plan")
                self.assertLess(download, verify)
                self.assertLess(verify, cli)
                self.assertLess(cli, deploy)
                self.assertIn(f"--expected-target {target}", section)
                self.assertIn("digest-mismatch: error", section)

    def test_plan_artifact_verifier_is_local_bounded_and_fail_closed(self):
        source = PLAN_VERIFIER.read_text(encoding="utf-8")

        required = [
            "ExpectedProvenance",
            "artifact_contains_unexpected_file",
            "artifact_contains_non_regular_entry",
            "plan_evidence_target_mismatch",
            "commit_provenance_mismatch",
            "run_attempt_provenance_mismatch",
            "bundle_plan_unexpected_shape",
            "MAX_PLAN_BYTES",
            "output_sha256",
            "github-oidc",
            "databricks-plan-review.json",
            "databricks-plan-review.md",
            "plan_review_not_accepted",
            "plan_review_recomputation_not_accepted",
            "plan_review_recomputation_mismatch",
            "load_plan_review_policy",
            "recompute_plan_review",
            "render_plan_review_summary",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("requests", source)

    def test_bundle_pins_direct_engine_and_supported_cli(self):
        bundle = BUNDLE.read_text(encoding="utf-8")

        self.assertIn("engine: direct", bundle)
        self.assertIn("databricks_cli_version: '>= 1.14.1, < 1.15.0'", bundle)

    def test_plan_capture_is_bounded_structured_and_rejects_static_secrets(self):
        script = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PLAN_SCRIPT, PLAN_CORE, PLAN_CAPTURE)
        )

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
        self.assertIn('PLAN_OUTPUT_FILE = "bundle-plan.json"', script)
        self.assertIn('command.extend(["--output", "json"])', script)
        self.assertIn("json.loads(completed.stdout)", script)
        self.assertIn('"format": "json"', script)
        self.assertIn("capture_plan_review", script)
        self.assertIn("databricks-plan-review.json", script)
        self.assertNotIn("check_output", script)
        self.assertNotIn("check_call", script)

    def test_documentation_requires_reviewed_plan_replay_and_approval(self):
        documentation = "\n".join(
            (
                DEPLOYMENT_DOC.read_text(encoding="utf-8"),
                APPLY_REVIEW_BRIEF.read_text(encoding="utf-8"),
            )
        )

        self.assertIn("A merge to `main` never deploys", documentation)
        self.assertIn("Leave `apply_changes` disabled", documentation)
        self.assertIn(
            "Review the completed `bundle validate` and structured `bundle plan`",
            documentation,
        )
        self.assertIn("DATABRICKS_AUTH_TYPE=github-oidc", documentation)
        self.assertIn("id-token: write", documentation)
        self.assertIn("actions: read", documentation)
        self.assertIn(
            "repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:dev",
            documentation,
        )
        self.assertIn("scripts/verify_bundle_plan_artifact.py", documentation)
        self.assertIn("databricks-plan-review.json", documentation)
        self.assertIn("independently recomputes", documentation)
        self.assertIn("current repository policy", documentation)
        self.assertIn(
            "databricks bundle deploy --target <target> --plan", documentation
        )
        self.assertIn("environment approval", documentation)
        self.assertIn("reuse the established Auto Loader checkpoint", documentation)
        self.assertIn("does not use a fixed landing filename", documentation)
        self.assertNotIn("does not cryptographically bind", documentation)


if __name__ == "__main__":
    unittest.main()
