import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY_NOTEBOOK = REPO_ROOT / "notebooks" / "04_quality_checks.py"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"


class QualityHistoryContractTest(unittest.TestCase):
    def test_quality_notebook_appends_metric_history(self):
        notebook_source = QUALITY_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("quality_metric_history", notebook_source)
        self.assertIn("quality_history_df", notebook_source)
        self.assertIn('"failed_error_check_count"', notebook_source)
        self.assertIn(".mode(\"append\")", notebook_source)

    def test_reporting_sql_exposes_quality_history(self):
        reporting_sql = REPORTING_SQL.read_text(encoding="utf-8")

        self.assertIn("quality_metric_history", reporting_sql)
        self.assertIn("all_error_checks_passed", reporting_sql)
        self.assertIn("LIMIT 30", reporting_sql)


if __name__ == "__main__":
    unittest.main()
