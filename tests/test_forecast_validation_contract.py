import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_forecast.py"
FORECAST_NOTEBOOK = REPO_ROOT / "notebooks" / "05_forecast_validation.py"
EXPECTATIONS_NOTEBOOK = (
    REPO_ROOT / "notebooks" / "06_lakeflow_quality_expectations.py"
)
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"
BUNDLE = REPO_ROOT / "databricks.yml"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"
REPORTING_ASSET = REPO_ROOT / "sql" / "reporting_assets" / "downtime_forecast.sql"
CHANGE_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "forecast_readiness_thresholds.md"
)
README = REPO_ROOT / "README.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"


class ForecastValidationContractTest(unittest.TestCase):
    def test_forecast_notebook_delegates_to_shared_calendar_logic(self):
        notebook_source = FORECAST_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("from lakehouse_demo.spark_forecast import", notebook_source)
        self.assertIn("ForecastConfig", notebook_source)
        self.assertIn("build_forecast_frames", notebook_source)
        self.assertIn('forecast_frames["validation"]', notebook_source)
        self.assertIn('forecast_frames["forecast"]', notebook_source)
        self.assertIn("gold_downtime_forecast_validation", notebook_source)
        self.assertIn("gold_downtime_forecast", notebook_source)
        self.assertIn(".saveAsTable(forecast_validation_table)", notebook_source)
        self.assertIn(".saveAsTable(forecast_table)", notebook_source)
        self.assertNotIn("Window.partitionBy", notebook_source)
        self.assertNotIn(".groupBy(", notebook_source)

    def test_shared_forecast_logic_requires_explicit_accuracy_thresholds(self):
        module_source = FORECAST_MODULE.read_text(encoding="utf-8")

        self.assertIn("rangeBetween(", module_source)
        self.assertIn("WINDOW_SEMANTICS = \"calendar_days\"", module_source)
        self.assertIn("STATUS_THRESHOLDS_NOT_CONFIGURED", module_source)
        self.assertIn("STATUS_ACCURACY_THRESHOLD_FAILED", module_source)
        self.assertIn("max_mae_downtime_minutes", module_source)
        self.assertIn("min_interval_coverage_pct", module_source)
        self.assertIn("must be configured together", module_source)
        self.assertIn("meets_mae_threshold", module_source)
        self.assertIn("meets_interval_coverage_threshold", module_source)
        self.assertLess(
            module_source.index("~F.col(\"meets_min_validation_samples\")"),
            module_source.index("F.lit(STATUS_VALIDATED)"),
        )
        self.assertLess(
            module_source.index("~F.col(\"thresholds_configured\")"),
            module_source.index("F.lit(STATUS_VALIDATED)"),
        )

    def test_workflow_passes_traceable_run_and_readiness_parameters(self):
        workflow_source = WORKFLOW.read_text(encoding="utf-8")

        forecast_task_pattern = (
            r"(?s)- task_key: forecast_validation.*?"
            r"depends_on:\s*\n\s*- task_key: quality_checks.*?"
            r"notebook_path: ../notebooks/05_forecast_validation.py"
        )

        self.assertRegex(workflow_source, forecast_task_pattern)
        self.assertIn(
            "baseline_window_days: ${var.baseline_window_days}",
            workflow_source,
        )
        self.assertIn(
            "forecast_horizon_days: ${var.forecast_horizon_days}",
            workflow_source,
        )
        self.assertIn(
            "min_validation_observations: ${var.min_validation_observations}",
            workflow_source,
        )
        self.assertIn(
            "max_mae_downtime_minutes: ${var.max_mae_downtime_minutes}",
            workflow_source,
        )
        self.assertIn(
            "min_interval_coverage_pct: ${var.min_interval_coverage_pct}",
            workflow_source,
        )
        self.assertIn('forecast_run_id: "job_{{job.run_id}}"', workflow_source)

    def test_bundle_exposes_optional_readiness_thresholds(self):
        bundle_source = BUNDLE.read_text(encoding="utf-8")

        self.assertIn("baseline_window_days:", bundle_source)
        self.assertIn("forecast_horizon_days:", bundle_source)
        self.assertIn("min_validation_observations:", bundle_source)
        self.assertIn("max_mae_downtime_minutes:", bundle_source)
        self.assertIn("min_interval_coverage_pct:", bundle_source)
        self.assertIn(
            "Leave blank to prevent client-ready validation",
            bundle_source,
        )

    def test_reporting_exposes_thresholds_and_run_provenance(self):
        for path in (REPORTING_SQL, REPORTING_ASSET):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("forecast_run_id", source)
                self.assertIn("window_semantics", source)
                self.assertIn("thresholds_configured", source)
                self.assertIn("max_mae_downtime_minutes", source)
                self.assertIn("min_interval_coverage_pct", source)
                self.assertIn("meets_mae_threshold", source)
                self.assertIn("meets_interval_coverage_threshold", source)
                self.assertIn("forecast_status", source)

    def test_expectations_accept_all_explicit_readiness_states(self):
        source = EXPECTATIONS_NOTEBOOK.read_text(encoding="utf-8")

        for status in (
            "validated_baseline",
            "insufficient_validation_history",
            "thresholds_not_configured",
            "accuracy_threshold_failed",
        ):
            self.assertIn(status, source)
        self.assertIn("forecast_run_id_present", source)
        self.assertIn("threshold_pair_consistent", source)
        self.assertIn("validated_status_is_evidenced", source)

    def test_change_brief_records_business_and_runtime_boundaries(self):
        source = CHANGE_BRIEF.read_text(encoding="utf-8")

        self.assertIn("calendar-day", source)
        self.assertIn("accuracy thresholds", source)
        self.assertIn("does not retain forecast vintages", source)
        self.assertIn("Databricks runtime", source)
        self.assertIn("rollback", source.lower())

    def test_docs_still_describe_forecast_integration(self):
        readme = README.read_text(encoding="utf-8")
        architecture = ARCHITECTURE.read_text(encoding="utf-8")

        for source in [readme, architecture]:
            with self.subTest(source=source[:20]):
                self.assertIn("05_forecast_validation.py", source)
                self.assertIn("gold_downtime_forecast_validation", source)
                self.assertIn("gold_downtime_forecast", source)


if __name__ == "__main__":
    unittest.main()
