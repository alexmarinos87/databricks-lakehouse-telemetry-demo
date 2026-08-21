import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_identity.py"


class WarehouseIdentityContractTest(unittest.TestCase):
    def test_identity_audit_reconstructs_both_fact_families_through_dimensions(
        self,
    ):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn("UPTIME_IDENTITY_COLUMNS", source)
        self.assertIn("FAILURE_IDENTITY_COLUMNS", source)
        self.assertIn("reconstruct_uptime_fact_business_rows", source)
        self.assertIn("reconstruct_failure_fact_business_rows", source)
        for dimension_name in (
            "dim_date",
            "dim_machine",
            "dim_client",
            "dim_site",
            "dim_model",
            "dim_fault",
        ):
            self.assertIn(f'"{dimension_name}"', source)

        self.assertIn('F.col("date.date_day").alias("event_date")', source)
        self.assertIn('alias(f"fact_{column_name}")', source)
        self.assertIn('F.col("machine.machine_id").alias("machine_id")', source)
        self.assertIn('F.col("client.client_id").alias("client_id")', source)
        self.assertIn('F.col("site.site_id").alias("site_id")', source)
        self.assertIn('F.col("model.model").alias("model")', source)
        self.assertIn('F.col("fault.fault_code").alias("fault_code")', source)

    def test_identity_comparison_is_bidirectional_and_bounded(self):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn('"missing_fact_identity"', source)
        self.assertIn('"unexpected_fact_identity"', source)
        self.assertGreaterEqual(source.count('"left_anti"'), 2)
        self.assertIn("count=int(missing)", source)
        self.assertIn("count=int(unexpected)", source)


if __name__ == "__main__":
    unittest.main()
