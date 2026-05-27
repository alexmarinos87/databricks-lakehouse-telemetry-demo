import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_NOTEBOOK = REPO_ROOT / "notebooks" / "05_forecast_validation.py"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
BUNDLE = REPO_ROOT / "databricks.yml"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"
README = REPO_ROOT / "README.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"


class ForecastValidationContractTest(unittest.TestCase):
    def test_forecast_notebook_writes_backtest_and_forecast_tables(self):
        notebook_source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("gold_machine_uptime", notebook_source)
        self.assertIn("gold_downtime_forecast_validation", notebook_source)
        self.assertIn("gold_downtime_forecast", notebook_source)
        self.assertIn("rolling_mean_baseline", notebook_source)
        self.assertIn("absolute_error_minutes", notebook_source)
        self.assertIn("prediction_interval_lower_minutes", notebook_source)
        self.assertIn("covered_by_validation_interval", notebook_source)
        self.assertIn("backtest_interval_coverage_pct", notebook_source)
        self.assertIn("forecast_status", notebook_source)
        self.assertIn(".saveAsTable(forecast_validation_table)", notebook_source)
        self.assertIn(".saveAsTable(forecast_table)", notebook_source)

    def test_workflow_runs_forecast_after_quality_gate(self):
        workflow_source = WORKFLOW.read_text(encoding="utf-8")

        forecast_task_pattern = (
            r"(?s)- task_key: forecast_validation.*?"
            r"depends_on:\s*\n\s*- task_key: quality_checks.*?"
            r"notebook_path: ../notebooks/05_forecast_validation.py"
        )

        self.assertRegex(workflow_source, forecast_task_pattern)
        self.assertIn("baseline_window_days: ${var.baseline_window_days}", workflow_source)
        self.assertIn("forecast_horizon_days: ${var.forecast_horizon_days}", workflow_source)
        self.assertIn("min_validation_observations: ${var.min_validation_observations}", workflow_source)

    def test_bundle_exposes_forecast_parameters(self):
        bundle_source = BUNDLE.read_text(encoding="utf-8")

        self.assertIn("baseline_window_days:", bundle_source)
        self.assertIn("forecast_horizon_days:", bundle_source)
        self.assertIn("min_validation_observations:", bundle_source)

    def test_reporting_sql_exposes_forecast_and_validation_context(self):
        reporting_sql = REPORTING_SQL.read_text(encoding="utf-8")

        self.assertIn("gold_downtime_forecast", reporting_sql)
        self.assertIn("gold_downtime_forecast_validation", reporting_sql)
        self.assertIn("prediction_interval_upper_minutes", reporting_sql)
        self.assertIn("forecast_status", reporting_sql)
        self.assertIn("mae_downtime_minutes", reporting_sql)
        self.assertIn("rmse_downtime_minutes", reporting_sql)
        self.assertIn("backtest_interval_coverage_pct", reporting_sql)

    def test_docs_describe_forecast_validation_integration(self):
        readme = README.read_text(encoding="utf-8")
        architecture = ARCHITECTURE.read_text(encoding="utf-8")

        for source in [readme, architecture]:
            with self.subTest(source=source[:20]):
                self.assertIn("05_forecast_validation.py", source)
                self.assertIn("gold_downtime_forecast_validation", source)
                self.assertIn("gold_downtime_forecast", source)


if __name__ == "__main__":
    unittest.main()
