import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestExternalBootstrapContract(unittest.TestCase):
    def test_github_bootstrap(self):
        source = (
            ROOT / "scripts/bootstrap_github_governance.py"
        ).read_text()
        self.assertIn(
            'mode.add_argument("--apply", action="store_true")',
            source,
        )
        self.assertIn(
            'mode.add_argument("--verify", action="store_true")',
            source,
        )
        self.assertIn(
            'os.environ.get("GITHUB_ADMIN_TOKEN", "")',
            source,
        )
        self.assertNotIn("--token", source)
        self.assertIn(
            "required_conversation_resolution",
            source,
        )
        self.assertIn("write_operations", source)

    def test_databricks_bootstrap(self):
        source = (
            ROOT / "scripts/bootstrap_databricks_oidc.py"
        ).read_text()
        self.assertIn(
            "service-principal-federation-policy",
            source,
        )
        self.assertIn('"service-principals", "get"', source)
        self.assertIn(
            'mode.add_argument("--verify", action="store_true")',
            source,
        )
        self.assertIn(
            "https://token.actions.githubusercontent.com",
            source,
        )
        self.assertNotIn("DATABRICKS_TOKEN", source)
        self.assertIn("read_back_verified", source)

    def test_plan_command_is_owner_only_and_plan_only(self):
        workflow = (
            ROOT / ".github/workflows/plan-evidence-command.yml"
        ).read_text()
        for token in [
            "github.event.issue.number == 44",
            (
                "github.event.comment.user.login == "
                "github.repository_owner"
            ),
            (
                "github.event.comment.author_association "
                "== 'OWNER'"
            ),
            (
                "github.event.comment.body == "
                "'/databricks-plan dev'"
            ),
            "environment: dev-plan",
            "id-token: write",
            "--mode plan",
            "issues: write",
        ]:
            self.assertIn(token, workflow)
        self.assertNotIn("bundle deploy", workflow)
        self.assertNotIn("upload_ingestion_plan", workflow)

    def test_docs(self):
        source = (
            ROOT / "docs/external_bootstrap.md"
        ).read_text()
        for token in [
            "GITHUB_ADMIN_TOKEN",
            "databricks auth login",
            "--verify",
            "/databricks-plan dev",
            "Do not commit",
            "apply_changes",
            "write operations",
        ]:
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
