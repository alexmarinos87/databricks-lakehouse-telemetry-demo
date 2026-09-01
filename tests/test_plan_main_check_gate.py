import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "plan-evidence-command.yml"
VERIFIER = ROOT / "scripts" / "verify_main_check_runs.py"
RUNBOOK = ROOT / "docs" / "accepted_main_check_evidence.md"
CHANGE_BRIEF = (
    ROOT
    / "docs"
    / "change_briefs"
    / "enforce_accepted_main_checks_before_plan.md"
)

DUAL_GATE_TOKENS = (
    "steps.readiness.outcome == 'success'",
    "steps.readiness-result.outputs.status == 'ready'",
    "steps.delivery-checks.outcome == 'success'",
    "steps.delivery-check-result.outputs.status == 'verified'",
)


class PlanMainCheckGateTest(unittest.TestCase):
    def source(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_grants_only_the_new_check_read_permission(self):
        source = self.source()
        job_permissions = source.split("    permissions:\n", 1)[1].split(
            "    env:\n", 1
        )[0]

        self.assertIn("checks: read", job_permissions)
        self.assertIn("contents: read", job_permissions)
        self.assertIn("issues: write", job_permissions)
        self.assertIn("id-token: write", job_permissions)
        self.assertNotIn("checks: write", source)
        self.assertNotIn("actions: write", source)
        self.assertNotIn("contents: write", source)

    def test_exact_accepted_main_checks_run_before_databricks_cli(self):
        source = self.source()

        accepted = source.index("Verify checked-out ref")
        readiness = source.index("Check protected-main external readiness")
        checks = source.index("Verify accepted main delivery checks")
        cli = source.index("Install Databricks CLI")
        plan = source.index("Capture authenticated dev plan evidence")
        retain = source.index("Retain dev plan evidence")
        enforce = source.index(
            "Enforce external readiness gate and accepted-main checks"
        )

        self.assertLess(accepted, readiness)
        self.assertLess(readiness, checks)
        self.assertLess(checks, cli)
        self.assertLess(cli, plan)
        self.assertLess(plan, retain)
        self.assertLess(retain, enforce)
        self.assertEqual(2, source.count("continue-on-error: true"))
        self.assertIn("id: delivery-checks", source)
        self.assertIn("scripts/verify_main_check_runs.py", source)
        self.assertIn('--commit "$(cat accepted-main-sha.txt)"', source)
        self.assertIn('--output-dir "${PLAN_EVIDENCE_DIR}"', source)
        self.assertIn("--timeout-seconds 30", source)

    def test_cli_and_plan_each_require_both_fail_closed_gates(self):
        source = self.source()
        cli_section = source.split("      - name: Install Databricks CLI", 1)[1].split(
            "      - name: Capture authenticated dev plan evidence", 1
        )[0]
        plan_section = source.split(
            "      - name: Capture authenticated dev plan evidence", 1
        )[1].split("      - name: Retain dev plan evidence", 1)[0]

        for label, section in (("cli", cli_section), ("plan", plan_section)):
            with self.subTest(section=label):
                for token in DUAL_GATE_TOKENS:
                    self.assertIn(token, section)

        enforcement = source.split(
            "      - name: Enforce external readiness gate and accepted-main checks",
            1,
        )[1].split("      - name: Record plan result on bootstrap issue", 1)[0]
        for token in (
            "steps.readiness.outcome != 'success'",
            "steps.readiness-result.outputs.status != 'ready'",
            "steps.delivery-checks.outcome != 'success'",
            "steps.delivery-check-result.outputs.status != 'verified'",
            "External readiness and accepted-main delivery checks must both pass",
        ):
            self.assertIn(token, enforcement)

    def test_missing_or_malformed_check_evidence_cannot_be_relabelled_verified(self):
        source = self.source()
        loader = source.split(
            "      - name: Load accepted main delivery-check result", 1
        )[1].split("      - name: Install Databricks CLI", 1)[0]

        self.assertIn(
            'blockers = ["accepted_main_check_evidence_missing"]',
            loader,
        )
        self.assertIn(
            'raw_status in {"verified", "blocked", "failed"}',
            loader,
        )
        self.assertIn('item.get("verified") is True', loader)
        self.assertIn('"Round-trip synthetic review evidence"', loader)
        self.assertNotIn('status = "verified"', loader)

    def test_sanitized_evidence_is_retained_and_reported(self):
        source = self.source()

        self.assertIn("main-check-runs-verification.json", source)
        self.assertIn("path: ${{ env.PLAN_EVIDENCE_DIR }}", source)
        self.assertIn("if-no-files-found: error", source)
        self.assertIn("retention-days: 14", source)
        self.assertIn("overwrite: false", source)
        for token in (
            "ARTIFACT_COMPATIBILITY_REQUIRED:",
            "ALL_REQUIRED_CONTEXTS_ACTIVE:",
            "DELIVERY_CHECK_STATUS:",
            "DELIVERY_CHECK_BLOCKERS:",
            "VALIDATE_VERIFIED:",
            "ARTIFACT_COMPATIBILITY_VERIFIED:",
            "accepted-main checks:",
            "accepted-main check blockers:",
            "accepted-main validate passed:",
            "accepted-main artifact compatibility passed:",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_plan_command_remains_owner_only_plan_only_and_secretless(self):
        source = self.source()

        self.assertIn("github.event.issue.number == 44", source)
        self.assertIn(
            "github.event.comment.user.login == github.repository_owner",
            source,
        )
        self.assertIn(
            "github.event.comment.author_association == 'OWNER'",
            source,
        )
        self.assertIn(
            "github.event.comment.body == '/databricks-plan dev'",
            source,
        )
        self.assertIn("environment: dev-plan", source)
        self.assertIn("DATABRICKS_AUTH_TYPE: github-oidc", source)
        self.assertNotIn("DATABRICKS_CLIENT_SECRET", source)
        self.assertNotIn("bundle deploy", source)
        self.assertNotIn("upload_ingestion_plan", source)
        self.assertNotIn("apply_changes", source)
        self.assertNotIn("run_workflow", source)

    def test_verifier_and_documentation_preserve_the_read_only_boundary(self):
        verifier = VERIFIER.read_text(encoding="utf-8")
        documentation = "\n".join(
            (
                RUNBOOK.read_text(encoding="utf-8"),
                CHANGE_BRIEF.read_text(encoding="utf-8"),
            )
        )

        self.assertIn('method="GET"', verifier)
        self.assertNotIn('method="POST"', verifier)
        self.assertNotIn('parser.add_argument("--token"', verifier)
        self.assertIn("checks: read", documentation)
        self.assertIn("before Databricks CLI installation", documentation)
        self.assertIn("exact accepted commit", documentation)
        self.assertIn("does not deploy", documentation)
        self.assertIn("human acceptance", documentation)


if __name__ == "__main__":
    unittest.main()
