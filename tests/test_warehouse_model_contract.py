import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"
WAREHOUSE_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_warehouse.py"
IDENTITY_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_identity.py"
WORKFLOW = REPO_ROOT / "resources" / "lakehouse_workflow.yml"


class WarehouseModelContractTest(unittest.TestCase):
    def test_notebook_delegates_to_shared_construction_and_publication_audit(self):
        notebook = NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("build_warehouse_frames", notebook)
        self.assertIn("audit_warehouse_publication", notebook)
        self.assertIn("gold_machine_uptime", notebook)
        self.assertIn("gold_failure_events", notebook)
        self.assertIn("fact_machine_uptime_daily", notebook)
        self.assertIn("fact_machine_failure_event", notebook)
        self.assertLess(
            notebook.index("findings = audit_warehouse_publication("),
            notebook.index(".saveAsTable("),
        )

    def test_shared_module_builds_the_uptime_star_schema(self):
        module = WAREHOUSE_MODULE.read_text(encoding="utf-8")

        for dataset_name in (
            "dim_client",
            "dim_date",
            "dim_machine",
            "dim_model",
            "dim_site",
            "fact_machine_uptime_daily",
        ):
            self.assertIn(f'"{dataset_name}"', module)

        self.assertIn('F.xxhash64("event_date", "machine_id")', module)
        self.assertIn('F.xxhash64("client_id")', module)
        self.assertIn('F.xxhash64("machine_id")', module)
        self.assertIn('F.xxhash64("model")', module)
        self.assertIn('F.xxhash64("client_id", "site_id")', module)
        self.assertIn('F.date_format("date_day", "yyyyMMdd")', module)
        self.assertIn('F.date_format("date_day", "yyyyMM")', module)
        self.assertIn('F.date_format("date_day", "yyyy-MM")', module)
        self.assertIn('F.dayofweek("date_day")', module)
        self.assertIn('F.date_format("date_day", "EEEE")', module)
        self.assertIn('"is_weekend"', module)
        self.assertIn('"uptime_fact_key"', module)
        self.assertIn('"downtime_pct"', module)
        self.assertIn('"idle_pct"', module)
        self.assertIn('"maintenance_pct"', module)
        self.assertIn('F.col("observed_minutes") > 0', module)

    def test_shared_modules_build_and_audit_failure_facts(self):
        module = WAREHOUSE_MODULE.read_text(encoding="utf-8")
        identity = IDENTITY_MODULE.read_text(encoding="utf-8")

        self.assertIn('"dim_fault"', module)
        self.assertIn('"fact_machine_failure_event"', module)
        self.assertIn('F.xxhash64("fault_code", "severity")', module)
        self.assertIn('F.xxhash64("event_id")', module)
        self.assertIn('"failure_event_count", F.lit(1)', module)
        self.assertIn('"maintenance_cost_gbp"', module)
        self.assertIn('"part_quantity"', module)
        self.assertIn("source_fact_count_mismatch", module)
        self.assertIn("duplicate_fact_grain", module)
        self.assertIn("null_dimension_key", module)
        self.assertIn("unmatched_dimension_key", module)
        self.assertIn('"left_anti"', module)
        self.assertIn("conflicting machine assignments", module)
        self.assertIn("missing_fact_identity", identity)
        self.assertIn("unexpected_fact_identity", identity)
        self.assertIn("audit_warehouse_publication", identity)

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
