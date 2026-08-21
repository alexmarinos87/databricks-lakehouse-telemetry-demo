import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEASURE_MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_measures.py"
PUBLICATION_MODULE = (
    REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_publication.py"
)
NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"


class WarehouseMeasureContractTest(unittest.TestCase):
    def test_measure_audit_covers_direct_derived_and_failure_values(self):
        source = MEASURE_MODULE.read_text(encoding="utf-8")

        for measure_name in (
            "event_date",
            "running_minutes",
            "idle_minutes",
            "maintenance_minutes",
            "downtime_minutes",
            "observed_minutes",
            "uptime_pct",
            "idle_pct",
            "downtime_pct",
            "maintenance_pct",
            "avg_health_score",
            "event_ts_utc",
            "failure_event_count",
            "temperature_c",
            "vibration_mm_s",
            "maintenance_cost_gbp",
            "part_code",
            "part_quantity",
        ):
            self.assertIn(f'"{measure_name}"', source)

        self.assertIn("def _percentage(", source)
        self.assertIn("eqNullSafe", source)
        self.assertIn("F.sum(", source)
        self.assertIn('"measure_mismatch"', source)
        self.assertIn('dataset=f"{dataset}.{measure_name}"', source)

    def test_publication_audit_composes_all_three_control_families(self):
        source = PUBLICATION_MODULE.read_text(encoding="utf-8")

        self.assertIn("def audit_warehouse_publication(", source)
        self.assertIn("*audit_warehouse(", source)
        self.assertIn("*audit_warehouse_identity(", source)
        self.assertIn("*audit_warehouse_measures(", source)
        self.assertIn("return tuple(sorted(findings))", source)

    def test_notebook_runs_composite_audit_before_the_first_write(self):
        notebook = NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn(
            "from lakehouse_demo.warehouse_publication import",
            notebook,
        )
        self.assertIn("audit_warehouse_publication", notebook)
        self.assertIn(
            "findings = audit_warehouse_publication(",
            notebook,
        )
        self.assertIn("measure-level", notebook)
        self.assertLess(
            notebook.index("findings = audit_warehouse_publication("),
            notebook.index(".saveAsTable("),
        )
        self.assertNotIn(
            "from lakehouse_demo.warehouse_identity import",
            notebook,
        )


if __name__ == "__main__":
    unittest.main()
