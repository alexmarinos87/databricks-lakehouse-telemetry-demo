import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PLAN_COMMAND_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "plan-evidence-command.yml"
)
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SPARK_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "spark-runtime.yml"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile.ci"
BUNDLE = REPO_ROOT / "databricks.yml"
WORKFLOW_RESOURCE = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
PIPELINE_RESOURCE = REPO_ROOT / "resources" / "lakehouse_quality_expectations.yml"
ACCESS_CONTROLS = REPO_ROOT / "resources" / "access_controls.yml"
SQL_REPORTING = REPO_ROOT / "resources" / "sql_reporting.yml"
QUERY_SCRIPT = REPO_ROOT / "scripts" / "upsert_reporting_queries.py"
GRANT_SCRIPT = REPO_ROOT / "scripts" / "apply_uc_grants.py"
PLAN_SCRIPT = REPO_ROOT / "scripts" / "capture_databricks_plan.py"
QUERY_MANIFEST = REPO_ROOT / "sql" / "reporting_assets" / "manifest.json"
FAILURE_QUERY = REPO_ROOT / "sql" / "reporting_assets" / "failure_events_by_fault.sql"

CHECKOUT_SHA = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_CLI_SHA = "databricks/setup-cli@602f285bac0c85e5985bf4c16d5a2befed0578d9"
DATABRICKS_CLI_VERSION = "1.14.1"
SETUP_CLI_STEP = (
    f"uses: {SETUP_CLI_SHA}\n"
    "        with:\n"
    f'          version: "{DATABRICKS_CLI_VERSION}"'
)
UPLOAD_ARTIFACT_SHA = (
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
)
PYTHON_IMAGE = (
    "python:3.11-slim@sha256:"
    "9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7"
)


class DeploymentContractTest(unittest.TestCase):
    def test_docker_validation_entrypoint_exists(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(PYTHON_IMAGE, dockerfile)
        self.assertIn("scripts/run_local_checks.sh", dockerfile)
        self.assertIn("docker build -f Dockerfile.ci", ci_workflow)
        self.assertIn("docker run --rm lakehouse-demo-ci", ci_workflow)

    def test_external_actions_and_validation_image_are_immutable(self):
        workflows = {
            "ci": CI_WORKFLOW.read_text(encoding="utf-8"),
            "spark": SPARK_WORKFLOW.read_text(encoding="utf-8"),
            "deploy": DEPLOY_WORKFLOW.read_text(encoding="utf-8"),
            "plan-command": PLAN_COMMAND_WORKFLOW.read_text(encoding="utf-8"),
        }

        for label, workflow in workflows.items():
            with self.subTest(workflow=label):
                self.assertIn(CHECKOUT_SHA, workflow)
                self.assertNotIn("actions/checkout@v", workflow)
                self.assertNotIn("runs-on: ubuntu-latest", workflow)
                self.assertIn("runs-on: ubuntu-24.04", workflow)

        deploy = workflows["deploy"]
        plan_command = workflows["plan-command"]
        self.assertEqual(4, deploy.count(SETUP_CLI_STEP))
        self.assertEqual(1, plan_command.count(SETUP_CLI_STEP))
        self.assertEqual(6, deploy.count(UPLOAD_ARTIFACT_SHA))
        self.assertEqual(1, plan_command.count(UPLOAD_ARTIFACT_SHA))
        self.assertNotIn("actions/upload-artifact@v", deploy)
        self.assertNotIn("actions/upload-artifact@v", plan_command)
        self.assertNotIn("databricks/setup-cli@main", deploy)
        self.assertNotIn("databricks/setup-cli@main", plan_command)
        self.assertNotIn("snapshot: true", deploy)
        self.assertNotIn("snapshot: true", plan_command)
        self.assertGreaterEqual(deploy.count("timeout-minutes:"), 5)

    def test_databricks_cli_version_matches_the_bundle_compatibility_window(self):
        bundle = BUNDLE.read_text(encoding="utf-8")
        deploy = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        plan_command = PLAN_COMMAND_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "databricks_cli_version: '>= 1.14.1, < 1.15.0'",
            bundle,
        )
        self.assertEqual(4, deploy.count(f'version: "{DATABRICKS_CLI_VERSION}"'))
        self.assertEqual(
            1,
            plan_command.count(f'version: "{DATABRICKS_CLI_VERSION}"'),
        )
        self.assertNotIn("version: latest", deploy.lower())
        self.assertNotIn("version: latest", plan_command.lower())

    def test_dependency_updates_cover_actions_docker_and_python(self):
        dependabot = DEPENDABOT.read_text(encoding="utf-8")

        self.assertIn("package-ecosystem: github-actions", dependabot)
        self.assertIn("package-ecosystem: docker", dependabot)
        self.assertIn("package-ecosystem: pip", dependabot)
        self.assertGreaterEqual(dependabot.count("interval: weekly"), 3)

    def test_deploy_workflow_has_test_diff_and_deploy_stages(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("test:", workflow)
        self.assertIn("diff-dev:", workflow)
        self.assertIn("deploy-dev:", workflow)
        self.assertIn("diff-prod:", workflow)
        self.assertIn("deploy-prod:", workflow)
        self.assertIn("environment: prod", workflow)
        self.assertEqual(6, workflow.count("scripts/capture_databricks_plan.py"))
        self.assertEqual(2, workflow.count("--mode plan"))
        self.assertIn("databricks bundle deploy -t prod", workflow)
        self.assertNotIn("databricks bundle plan -t", workflow)
        self.assertIn(
            "DEFAULT_PLAN_TIMEOUT_SECONDS",
            PLAN_SCRIPT.read_text(encoding="utf-8"),
        )
        self.assertGreaterEqual(workflow.count("--statement-timeout-seconds 180"), 4)
        self.assertGreaterEqual(workflow.count("--command-timeout-seconds 60"), 6)
        self.assertGreaterEqual(workflow.count("--poll-interval-seconds 5"), 4)

    def test_grant_helper_bounds_commands_and_statement_polling(self):
        grant_script = GRANT_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("DEFAULT_COMMAND_TIMEOUT_SECONDS", grant_script)
        self.assertIn("DEFAULT_STATEMENT_TIMEOUT_SECONDS", grant_script)
        self.assertIn("DEFAULT_POLL_INTERVAL_SECONDS", grant_script)
        self.assertIn("subprocess.run(", grant_script)
        self.assertIn("timeout=timeout_seconds", grant_script)
        self.assertIn("time.monotonic", grant_script)
        self.assertIn("/cancel", grant_script)
        self.assertIn("--statement-timeout-seconds", grant_script)
        self.assertNotIn("subprocess.check_output", grant_script)
        self.assertNotIn("Statement failed:", grant_script)

    def test_query_publisher_is_bounded_paginated_and_fail_closed(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        query_script = QUERY_SCRIPT.read_text(encoding="utf-8")

        self.assertGreaterEqual(workflow.count("--command-timeout-seconds 60"), 6)
        self.assertIn("DEFAULT_COMMAND_TIMEOUT_SECONDS", query_script)
        self.assertIn("QUERY_PAGE_SIZE", query_script)
        self.assertIn("MAX_QUERY_PAGES", query_script)
        self.assertIn("MAX_REPORTING_ASSETS", query_script)
        self.assertIn("MAX_ASSET_BYTES", query_script)
        self.assertIn("subprocess.run(", query_script)
        self.assertIn("timeout=timeout_seconds", query_script)
        self.assertIn('"--page-size"', query_script)
        self.assertIn('"--page-token"', query_script)
        self.assertIn("Multiple active queries", query_script)
        self.assertIn("duplicate display name", query_script)
        self.assertNotIn("subprocess.check_output", query_script)
        self.assertNotIn("subprocess.check_call", query_script)

    def test_bundle_exposes_access_control_principals(self):
        bundle = BUNDLE.read_text(encoding="utf-8")

        self.assertIn("admin_group_name:", bundle)
        self.assertIn("engineer_group_name:", bundle)
        self.assertIn("analyst_group_name:", bundle)
        self.assertIn("ci_service_principal_name:", bundle)

    def test_job_pipeline_and_sql_warehouse_have_permissions(self):
        workflow_resource = WORKFLOW_RESOURCE.read_text(encoding="utf-8")
        pipeline_resource = PIPELINE_RESOURCE.read_text(encoding="utf-8")
        sql_reporting = SQL_REPORTING.read_text(encoding="utf-8")

        for source in [workflow_resource, pipeline_resource, sql_reporting]:
            with self.subTest(source=source[:40]):
                self.assertIn("permissions:", source)
                self.assertIn("${var.admin_group_name}", source)
                self.assertIn("${var.engineer_group_name}", source)
                self.assertIn("${var.analyst_group_name}", source)
                self.assertIn("${var.ci_service_principal_name}", source)

    def test_unity_catalog_grants_are_bundle_managed(self):
        access_controls = ACCESS_CONTROLS.read_text(encoding="utf-8")
        grant_script = GRANT_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("schemas:", access_controls)
        self.assertIn("volumes:", access_controls)
        self.assertIn("USE_SCHEMA", access_controls)
        self.assertIn("SELECT", access_controls)
        self.assertIn("READ_VOLUME", access_controls)
        self.assertIn("WRITE_VOLUME", access_controls)
        self.assertIn("REPORTING_TABLES", grant_script)
        self.assertIn("REPORTING_VIEWS", grant_script)
        self.assertIn("REPORTING_MATERIALIZED_VIEWS", grant_script)
        self.assertIn('object_type="TABLE"', grant_script)
        self.assertIn('object_type="VIEW"', grant_script)
        self.assertIn('object_type="MATERIALIZED VIEW"', grant_script)
        self.assertIn("/api/2.0/sql/statements", grant_script)
        self.assertIn("fact_machine_failure_event", grant_script)
        self.assertIn("dim_fault", grant_script)

    def test_sql_reporting_assets_are_published_after_deploy(self):
        deploy_workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        query_script = QUERY_SCRIPT.read_text(encoding="utf-8")
        manifest = QUERY_MANIFEST.read_text(encoding="utf-8")
        failure_query = FAILURE_QUERY.read_text(encoding="utf-8")

        self.assertIn("Publish dev SQL queries", deploy_workflow)
        self.assertIn("Publish prod SQL queries", deploy_workflow)
        self.assertIn("Apply dev reporting table grants", deploy_workflow)
        self.assertIn("Apply prod reporting table grants", deploy_workflow)
        self.assertIn('"queries"', query_script)
        self.assertIn('"create"', query_script)
        self.assertIn('"update"', query_script)
        self.assertIn('"permissions"', query_script)
        self.assertIn("daily_uptime_by_site_model.sql", manifest)
        self.assertIn("downtime_forecast.sql", manifest)
        self.assertIn("failure_events_by_fault.sql", manifest)
        self.assertIn("fact_machine_failure_event", failure_query)
        self.assertIn("dim_fault", failure_query)
        self.assertIn("affected_machine_count", failure_query)


if __name__ == "__main__":
    unittest.main()
