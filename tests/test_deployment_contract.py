import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile.ci"
BUNDLE = REPO_ROOT / "databricks.yml"
WORKFLOW_RESOURCE = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
PIPELINE_RESOURCE = REPO_ROOT / "resources" / "lakehouse_quality_expectations.yml"
ACCESS_CONTROLS = REPO_ROOT / "resources" / "access_controls.yml"
SQL_REPORTING = REPO_ROOT / "resources" / "sql_reporting.yml"
QUERY_SCRIPT = REPO_ROOT / "scripts" / "upsert_reporting_queries.py"
GRANT_SCRIPT = REPO_ROOT / "scripts" / "apply_uc_grants.py"
QUERY_MANIFEST = REPO_ROOT / "sql" / "reporting_assets" / "manifest.json"
FAILURE_QUERY = REPO_ROOT / "sql" / "reporting_assets" / "failure_events_by_fault.sql"


class DeploymentContractTest(unittest.TestCase):
    def test_docker_validation_entrypoint_exists(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("python:3.11-slim", dockerfile)
        self.assertIn("scripts/run_local_checks.sh", dockerfile)
        self.assertIn("docker build -f Dockerfile.ci", ci_workflow)
        self.assertIn("docker run --rm lakehouse-demo-ci", ci_workflow)

    def test_deploy_workflow_has_test_diff_and_deploy_stages(self):
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("test:", workflow)
        self.assertIn("diff-dev:", workflow)
        self.assertIn("deploy-dev:", workflow)
        self.assertIn("diff-prod:", workflow)
        self.assertIn("deploy-prod:", workflow)
        self.assertIn("environment: prod", workflow)
        self.assertIn("databricks bundle plan -t dev", workflow)
        self.assertIn("databricks bundle deploy -t prod", workflow)

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
        self.assertIn("GRANT SELECT ON TABLE", grant_script)
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
