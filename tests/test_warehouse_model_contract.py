import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"


class WarehouseModelContractTest(unittest.TestCase):
    def test_star_schema_is_built_from_gold(self):
        notebook = NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("gold_machine_uptime", notebook)
        self.assertIn("dim_client", notebook)
        self.assertIn("dim_date", notebook)
        self.assertIn("dim_machine", notebook)
        self.assertIn("dim_model", notebook)
        self.assertIn("dim_site", notebook)
        self.assertIn("fact_machine_uptime_daily", notebook)
        self.assertIn("uptime_fact_key", notebook)
        self.assertIn('"downtime_pct"', notebook)
        self.assertIn('"idle_pct"', notebook)
        self.assertIn(
            'F.col("idle_minutes") / F.col("observed_minutes")',
            notebook,
        )
        self.assertIn('"maintenance_pct"', notebook)
        self.assertIn(
            'F.col("maintenance_minutes") / F.col("observed_minutes")',
            notebook,
        )
        self.assertIn('F.col("observed_minutes") > 0', notebook)
        self.assertIn(
            'F.xxhash64("event_date", "machine_id")',
            notebook,
        )
        self.assertIn('F.xxhash64("client_id")', notebook)
        self.assertIn('F.xxhash64("machine_id")', notebook)
        self.assertIn('F.xxhash64("model")', notebook)
        self.assertIn('F.xxhash64("client_id", "site_id")', notebook)
        self.assertIn('F.date_format("date_day", "yyyyMMdd").cast("int")', notebook)
        self.assertIn('F.date_format("date_day", "yyyyMM").cast("int")', notebook)
        self.assertIn('F.date_format("date_day", "yyyy-MM")', notebook)
        self.assertIn('F.dayofweek("date_day")', notebook)
        self.assertIn('F.date_format("date_day", "EEEE")', notebook)
        self.assertIn('"is_weekend"', notebook)
        self.assertIn('dates.select("date_day", "date_key")', notebook)
        self.assertIn(
            'sites.select("site_id", "client_id", "site_key")',
            notebook,
        )
        self.assertIn('clients.select("client_id", "client_key")', notebook)
        self.assertIn('models.select("model", "model_key")', notebook)

    def test_failure_star_schema_is_built_from_gold_failures(self):
        notebook = NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("gold_failure_events", notebook)
        self.assertIn("dim_fault", notebook)
        self.assertIn("fact_machine_failure_event", notebook)
        self.assertIn('F.xxhash64("fault_code", "severity")', notebook)
        self.assertIn('F.xxhash64("event_id")', notebook)
        self.assertIn('"failure_event_count", F.lit(1)', notebook)
        self.assertIn('"maintenance_cost_gbp"', notebook)
        self.assertIn('"part_quantity"', notebook)

    def test_warehouse_runs_between_gold_and_quality(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("task_key: warehouse_model", workflow)
        self.assertIn("notebook_path: ../notebooks/07_warehouse_model.py", workflow)
        self.assertIn(
            "task_key: quality_checks\n          depends_on:\n            - task_key: warehouse_model",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
