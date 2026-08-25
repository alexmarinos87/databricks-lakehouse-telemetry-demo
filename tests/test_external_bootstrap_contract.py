import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ARTIFACT_SHA = (
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
)


class TestExternalBootstrapContract(unittest.TestCase):
    def test_github_bootstrap(self):
        source = (ROOT / "scripts/bootstrap_github_governance.py").read_text()
        self.assertIn('parser.add_argument("--apply", action="store_true")', source)
        self.assertIn('os.environ.get("GITHUB_ADMIN_TOKEN", "")', source)
        self.assertNotIn("--token", source)
        self.assertIn("required_conversation_resolution", source)

    def test_databricks_bootstrap(self):
        source = (ROOT / "scripts/bootstrap_databricks_oidc.py").read_text()
        self.assertIn("service-principal-federation-policy", source)
        self.assertIn("https://token.actions.githubusercontent.com", source)
        self.assertNotIn("DATABRICKS_TOKEN", source)

    def test_github_governance_verifier_is_read_only_and_sanitized(self):
        source = (ROOT / "scripts/verify_github_governance.py").read_text()
        required_tokens = [
            'method="GET"',
            'os.environ.get("GITHUB_ADMIN_TOKEN", "")',
            "github-governance-verification.json",
            "DATABRICKS_RUNTIME_CLIENT_ID",
            "DATABRICKS_CLIENT_SECRET",
            "required_conversation_resolution",
            "main_branch_is_unprotected",
            "environment_branch_scope_drift",
            "static_client_secret_present",
            "MAX_PAGES",
            "inventory_is_truncated",
        ]
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn('parser.add_argument("--token"', source)
        self.assertNotIn('method="POST"', source)
        self.assertNotIn('method="PUT"', source)
        self.assertNotIn('method="PATCH"', source)
        self.assertNotIn('method="DELETE"', source)

    def test_databricks_federation_verifier_is_read_only_secretless_and_sanitized(self):
        source = (ROOT / "scripts/verify_databricks_federation.py").read_text()
        required_tokens = [
            '"service-principals",\n                "get"',
            '"service-principal-federation-policy",\n                "list"',
            '"service-principal-secrets",\n                "list"',
            "databricks-federation-verification.json",
            "DATABRICKS_TOKEN",
            "DATABRICKS_CLIENT_SECRET",
            "deployment and runtime numeric identities overlap",
            "service_principal_is_account_admin",
            "service_principal_has_oauth_secrets",
            "unexpected_federation_policy",
            "MAX_PAGES",
            "pagination_token_repeated",
        ]
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn('parser.add_argument("--token"', source)
        self.assertNotIn('"service-principals",\n                "create"', source)
        self.assertNotIn(
            '"service-principal-federation-policy",\n                "create"', source
        )
        self.assertNotIn(
            '"service-principal-secrets",\n                "create"', source
        )
        self.assertNotIn('"update"', source)
        self.assertNotIn('"delete"', source)
        self.assertNotIn('"patch"', source)

    def test_plan_command_is_owner_only_plan_only_and_readiness_gated(self):
        workflow = (
            ROOT / ".github" / "workflows" / "plan-evidence-command.yml"
        ).read_text()
        required_tokens = [
            "github.event.issue.number == 44",
            "github.event.comment.user.login == github.repository_owner",
            "github.event.comment.author_association == 'OWNER'",
            "github.event.comment.body == '/databricks-plan dev'",
            "environment: dev-plan",
            "id-token: write",
            "issues: write",
            "scripts/check_external_readiness.py",
            "continue-on-error: true",
            "steps.readiness.outcome == 'success'",
            "Enforce external readiness gate",
            "external-readiness.json",
            "READINESS_BLOCKERS",
            "--mode plan",
            UPLOAD_ARTIFACT_SHA,
        ]
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, workflow)

        readiness_position = workflow.index("scripts/check_external_readiness.py")
        cli_position = workflow.index("databricks/setup-cli@")
        plan_position = workflow.index("scripts/capture_databricks_plan.py")
        self.assertLess(readiness_position, cli_position)
        self.assertLess(readiness_position, plan_position)
        self.assertNotIn("bundle deploy", workflow)
        self.assertNotIn("upload_ingestion_plan", workflow)
        self.assertNotIn("DATABRICKS_CLIENT_SECRET:", workflow)

        readiness_source = (
            ROOT / "scripts" / "check_external_readiness.py"
        ).read_text()
        for token in [
            "accepted_main_does_not_match_workflow_sha",
            "github_api_url_is_not_allowed",
        ]:
            with self.subTest(readiness_token=token):
                self.assertIn(token, readiness_source)

    def test_docs(self):
        documentation = (ROOT / "docs" / "external_bootstrap.md").read_text()
        required_tokens = [
            "GITHUB_ADMIN_TOKEN",
            "databricks auth login",
            "/databricks-plan dev",
            "Do not commit",
            "apply_changes",
            "external-readiness.json",
            "before downloading the Databricks CLI",
            "scripts/verify_github_governance.py",
            ".bootstrap/runtime-identity.json",
            "github-governance-verification.json",
            "GET requests only",
            "status: verified",
            "scripts/verify_databricks_federation.py",
            "databricks-federation-verification.json",
            "service-principal-secrets list",
            "no OAuth client secret",
            "both independent verifiers record `status: verified`",
        ]
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, documentation)


if __name__ == "__main__":
    unittest.main()
