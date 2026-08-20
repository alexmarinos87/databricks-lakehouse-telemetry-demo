import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRONZE = REPO_ROOT / "notebooks" / "01_bronze_ingest.py"
SILVER = REPO_ROOT / "notebooks" / "02_silver_transform.py"
GOLD = REPO_ROOT / "notebooks" / "03_gold_models.py"
WAREHOUSE = REPO_ROOT / "notebooks" / "07_warehouse_model.py"


class NotebookRuntimeWiringTest(unittest.TestCase):
    def test_bronze_uses_the_shared_source_schema(self):
        notebook = BRONZE.read_text(encoding="utf-8")

        self.assertIn(
            "from lakehouse_demo.spark_medallion import raw_machine_event_schema",
            notebook,
        )
        self.assertIn("raw_schema = raw_machine_event_schema()", notebook)
        self.assertNotIn("StructField(", notebook)
        self.assertNotIn("StructType(", notebook)

    def test_transformation_notebooks_add_repository_source_path(self):
        for path in (SILVER, GOLD, WAREHOUSE):
            with self.subTest(path=path.name):
                notebook = path.read_text(encoding="utf-8")
                self.assertIn("def _add_project_src_to_path():", notebook)
                self.assertIn("_add_project_src_to_path()", notebook)
                self.assertIn('Path(workspace_root_text) / "src"', notebook)

    def test_silver_calls_shared_transform_and_reconciles_before_writes(self):
        notebook = SILVER.read_text(encoding="utf-8")

        self.assertIn("build_silver_frames", notebook)
        self.assertIn("reconcile_silver", notebook)
        self.assertIn("silver_frames = build_silver_frames(bronze)", notebook)
        self.assertIn('silver_frames["silver"]', notebook)
        self.assertIn('silver_frames["quarantine"]', notebook)
        self.assertIn("silver_machine_events", notebook)
        self.assertIn("silver_quarantine_machine_events", notebook)
        self.assertIn("reconciliation.has_conflicts", notebook)
        self.assertIn(
            "Silver publication blocked because conflicting payloads share",
            notebook,
        )

        reconciliation_position = notebook.index("reconcile_silver(")
        quarantine_write_position = notebook.index("quarantine.write.format")
        conflict_gate_position = notebook.index("if reconciliation.has_conflicts:")
        silver_write_position = notebook.index("silver.write.format")

        self.assertLess(reconciliation_position, quarantine_write_position)
        self.assertLess(quarantine_write_position, conflict_gate_position)
        self.assertLess(conflict_gate_position, silver_write_position)
        self.assertNotIn("Window.partitionBy", notebook)
        self.assertNotIn("F.to_timestamp", notebook)

    def test_gold_persists_every_shared_output(self):
        notebook = GOLD.read_text(encoding="utf-8")

        self.assertIn("build_gold_frames", notebook)
        self.assertIn("gold_frames = build_gold_frames(silver)", notebook)
        for dataset_name in (
            "gold_machine_uptime",
            "gold_failure_events",
            "gold_maintenance_costs",
            "gold_parts_usage",
            "gold_client_asset_summary",
        ):
            self.assertIn(f'"{dataset_name}"', notebook)
        self.assertLess(
            notebook.index("build_gold_frames(silver)"),
            notebook.index(".saveAsTable("),
        )
        self.assertNotIn(".groupBy(", notebook)

    def test_warehouse_audits_shared_outputs_before_publication(self):
        notebook = WAREHOUSE.read_text(encoding="utf-8")

        self.assertIn("build_warehouse_frames", notebook)
        self.assertIn("audit_warehouse_publication", notebook)
        self.assertIn(
            "warehouse_frames = build_warehouse_frames(gold_uptime, gold_failures)",
            notebook,
        )
        self.assertIn(
            "findings = audit_warehouse_publication(",
            notebook,
        )
        self.assertIn("Warehouse reconciliation failed before publication", notebook)
        for dataset_name in (
            "dim_client",
            "dim_date",
            "dim_fault",
            "dim_machine",
            "dim_model",
            "dim_site",
            "fact_machine_failure_event",
            "fact_machine_uptime_daily",
        ):
            self.assertIn(dataset_name, notebook)
        self.assertLess(
            notebook.index("findings = audit_warehouse_publication("),
            notebook.index(".saveAsTable("),
        )
        self.assertNotIn("findings = audit_warehouse(", notebook)
        self.assertNotIn("F.xxhash64", notebook)
        self.assertNotIn("dropDuplicates", notebook)


if __name__ == "__main__":
    unittest.main()
