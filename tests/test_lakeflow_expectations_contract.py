import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS_NOTEBOOK = REPO_ROOT / "notebooks" / "06_lakeflow_quality_expectations.py"
PIPELINE_RESOURCE = REPO_ROOT / "resources" / "lakehouse_quality_expectations.yml"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"
README = REPO_ROOT / "README.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"


class LakeflowExpectationsContractTest(unittest.TestCase):
    def test_expectations_notebook_defines_materialized_views(self):
        notebook_source = EXPECTATIONS_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("from pyspark import pipelines as dp", notebook_source)
        self.assertIn("@dp.materialized_view", notebook_source)
        self.assertIn("@dp.expect_all", notebook_source)
        self.assertIn("quality_expectation_silver_machine_events", notebook_source)
        self.assertIn("quality_expectation_gold_machine_uptime", notebook_source)
        self.assertIn("quality_expectation_downtime_forecast", notebook_source)
        self.assertIn("forecast_status_known", notebook_source)

    def test_pipeline_resource_points_to_expectations_notebook(self):
        resource_source = PIPELINE_RESOURCE.read_text(encoding="utf-8")

        self.assertIn("lakehouse_quality_expectations:", resource_source)
        self.assertIn("catalog: ${var.catalog}", resource_source)
        self.assertIn("schema: ${var.schema}", resource_source)
        self.assertIn("path: ../notebooks/06_lakeflow_quality_expectations.py", resource_source)
        self.assertIn("quality_expectation_event_log", resource_source)

    def test_workflow_refreshes_expectations_after_forecast(self):
        workflow_source = WORKFLOW.read_text(encoding="utf-8")

        expectation_task_pattern = (
            r"(?s)- task_key: quality_expectations_pipeline.*?"
            r"depends_on:\s*\n\s*- task_key: forecast_validation.*?"
            r"pipeline_id: \$\{resources\.pipelines\.lakehouse_quality_expectations\.id\}"
        )

        self.assertRegex(workflow_source, expectation_task_pattern)

    def test_reporting_sql_exposes_expectation_outputs(self):
        reporting_sql = REPORTING_SQL.read_text(encoding="utf-8")

        self.assertIn("quality_expectation_event_log", reporting_sql)
        self.assertIn("quality_expectation_silver_machine_events", reporting_sql)
        self.assertIn("quality_expectation_gold_machine_uptime", reporting_sql)
        self.assertIn("quality_expectation_downtime_forecast", reporting_sql)

    def test_docs_describe_expectations_integration(self):
        readme = README.read_text(encoding="utf-8")
        architecture = ARCHITECTURE.read_text(encoding="utf-8")

        for source in [readme, architecture]:
            with self.subTest(source=source[:20]):
                self.assertIn("06_lakeflow_quality_expectations.py", source)
                self.assertIn("quality_expectation_event_log", source)
                self.assertIn("quality_expectation_downtime_forecast", source)


if __name__ == "__main__":
    unittest.main()
