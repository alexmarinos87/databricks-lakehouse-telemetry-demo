import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"
WAREHOUSE_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_warehouse.py"
IDENTITY_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_identity.py"
MEASURE_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_measures.py"
PUBLICATION_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_publication.py"
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
            "dim_client", "dim_date", "dim_machine", "dim_model",
            "dim_site", "fact_machine_uptime_daily",
        ):
            self.assertIn(f'"{dataset_name}"', module)
        for token in (
            'F.xxhash64("event_date", "machine_id")',
            'F.xxhash64("client_id")',
            'F.xxhash64("machine_id", "valid_from_date")',
            'F.xxhash64("model")',
            'F.xxhash64("client_id", "site_id")',
            'F.date_format("date_day", "yyyyMMdd")',
            'F.date_format("date_day", "yyyyMM")',
            'F.date_format("date_day", "yyyy-MM")',
            'F.dayofweek("date_day")',
            'F.date_format("date_day", "EEEE")',
            '"is_weekend"', '"uptime_fact_key"',
            '"downtime_impact_ratio_pct"', '"idle_pct"',
            '"maintenance_pct"', 'downtime_impact_ratio()',
        ):
            self.assertIn(token, module)
        self.assertNotIn('"downtime_pct"', module)
        for version_column in (
            "assignment_version", "valid_from_date", "valid_to_date", "is_current",
        ):
            self.assertIn(f'"{version_column}"', module)

    def test_shared_modules_build_and_audit_failure_facts(self):
        module = WAREHOUSE_MODULE.read_text(encoding="utf-8")
        identity = IDENTITY_MODULE.read_text(encoding="utf-8")
        measures = MEASURE_MODULE.read_text(encoding="utf-8")
        publication = PUBLICATION_MODULE.read_text(encoding="utf-8")
        for token in (
            '"dim_fault"', '"fact_machine_failure_event"',
            'F.xxhash64("fault_code", "severity")', 'F.xxhash64("event_id")',
            '"failure_event_count", F.lit(1)', '"maintenance_cost_gbp"',
            '"part_quantity"', "source_fact_count_mismatch",
            "duplicate_fact_grain", "null_dimension_key",
            "unmatched_dimension_key", '"left_anti"',
            "conflicting same-day machine assignments",
        ):
            self.assertIn(token, module)
        self.assertIn("missing_fact_identity", identity)
        self.assertIn("unexpected_fact_identity", identity)
        self.assertIn("downtime_impact_ratio_pct", identity)
        self.assertIn("measure_mismatch", measures)
        self.assertIn("audit_warehouse_publication", publication)
        self.assertIn("audit_warehouse_identity", publication)
        self.assertIn("audit_warehouse_measures", publication)

    def test_warehouse_runs_between_gold_and_quality(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("task_key: warehouse_model", workflow)
        self.assertIn("notebook_path: ../notebooks/07_warehouse_model.py", workflow)
        self.assertIn(
            "task_key: quality_checks\n          depends_on:\n"
            "            - task_key: warehouse_model",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
