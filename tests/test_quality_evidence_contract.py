import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_quality.py"
NOTEBOOK = REPO_ROOT / "notebooks" / "04_quality_checks.py"
MONITORING_SQL = REPO_ROOT / "sql" / "reporting_assets" / "quality_monitoring.sql"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"


class QualityEvidenceContractTest(unittest.TestCase):
    def test_shared_quality_module_covers_medallion_and_warehouse(self):
        source = MODULE.read_text(encoding="utf-8")
        for table_name in (
            "bronze",
            "silver",
            "quarantine",
            "gold_machine_uptime",
            "gold_failure_events",
            "dim_client",
            "dim_date",
            "dim_fault",
            "dim_machine",
            "dim_model",
            "dim_site",
            "fact_machine_failure_event",
            "fact_machine_uptime_daily",
        ):
            self.assertIn(f'"{table_name}"', source)
        for check_name in (
            "silver_event_id_unique",
            "silver_required_fields_present",
            "silver_operational_metrics_in_bounds",
            "uptime_fact_grain_unique",
            "uptime_fact_dimension_keys_present",
            "uptime_fact_percentage_bounds",
            "uptime_fact_status_minutes_within_observed",
            "uptime_fact_downtime_semantics_review",
            "failure_fact_grain_unique",
            "failure_fact_dimension_keys_present",
            "failure_fact_measures_in_bounds",
        ):
            self.assertIn(f'"{check_name}"', source)
        self.assertIn('severity="warning"', source)
        self.assertIn("Quality check could not be evaluated", source)
        self.assertNotIn("str(exc)", source)

    def test_detailed_and_summary_evidence_share_one_run_identity(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn('StructField("quality_run_id"', source)
        self.assertIn('StructField("checked_at"', source)
        self.assertIn('StructField("observed_count"', source)
        self.assertIn('groupBy("quality_run_id", "checked_at")', source)
        self.assertIn('"failed_error_check_count"', source)
        self.assertIn('"failed_warning_check_count"', source)
        self.assertIn('"all_error_checks_passed"', source)

    def test_notebook_appends_evidence_before_raising(self):
        notebook = NOTEBOOK.read_text(encoding="utf-8")
        self.assertIn("candidate_frames", notebook)
        self.assertIn("unavailable_tables", notebook)
        self.assertIn("evaluate_quality_tables(", notebook)
        self.assertIn("quality_results_dataframe(", notebook)
        self.assertIn("summarize_quality_results(results_df)", notebook)
        self.assertGreaterEqual(notebook.count('.mode("append")'), 2)
        self.assertGreaterEqual(notebook.count('.option("mergeSchema", True)'), 2)
        self.assertLess(
            notebook.index("results_df.write.format"),
            notebook.index("quality_history_df.write.format"),
        )
        self.assertLess(
            notebook.index("quality_history_df.write.format"),
            notebook.index("if failed_error_checks:"),
        )
        self.assertNotIn('.mode("overwrite")', notebook)
        self.assertNotIn("str(exc)", notebook)

    def test_reporting_queries_expose_run_identity_and_bounded_counts(self):
        for path in (MONITORING_SQL, REPORTING_SQL):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("quality_run_id", source)
                self.assertIn("observed_count", source)
                self.assertIn("failed_error_check_count", source)
                self.assertIn("all_error_checks_passed", source)


if __name__ == "__main__":
    unittest.main()
