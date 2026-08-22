import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class TestExternalBootstrapContract(unittest.TestCase):
    def test_github_bootstrap(self):
        s=(ROOT/"scripts/bootstrap_github_governance.py").read_text()
        self.assertIn('parser.add_argument("--apply", action="store_true")',s)
        self.assertIn('os.environ.get("GITHUB_ADMIN_TOKEN", "")',s)
        self.assertNotIn("--token",s); self.assertIn("required_conversation_resolution",s)
    def test_databricks_bootstrap(self):
        s=(ROOT/"scripts/bootstrap_databricks_oidc.py").read_text()
        self.assertIn("service-principal-federation-policy",s)
        self.assertIn("https://token.actions.githubusercontent.com",s)
        self.assertNotIn("DATABRICKS_TOKEN",s)
    def test_plan_command_is_owner_only_and_plan_only(self):
        w=(ROOT/".github/workflows/plan-evidence-command.yml").read_text()
        for token in ["github.event.issue.number == 44","github.event.comment.user.login == github.repository_owner","github.event.comment.author_association == 'OWNER'","github.event.comment.body == '/databricks-plan dev'","environment: dev-plan","id-token: write","--mode plan","issues: write"]: self.assertIn(token,w)
        self.assertNotIn("bundle deploy",w); self.assertNotIn("upload_ingestion_plan",w)
    def test_docs(self):
        s=(ROOT/"docs/external_bootstrap.md").read_text()
        for token in ["GITHUB_ADMIN_TOKEN","databricks auth login","/databricks-plan dev","Do not commit","apply_changes"]: self.assertIn(token,s)
if __name__=="__main__": unittest.main()
