import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_identity.py"
NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"


class WarehouseIdentityContractTest(unittest.TestCase):
    def test_identity_audit_reconstructs_both_fact_families(self):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn("_UPTIME_IDENTITY", source)
        self.assertIn("_FAILURE_IDENTITY", source)
        self.assertIn("dim_machine", source)
        self.assertIn("dim_client", source)
        self.assertIn("dim_site", source)
        self.assertIn("dim_model", source)
        self.assertIn("dim_fault", source)
        self.assertIn('"missing_fact_identity"', source)
        self.assertIn('"unexpected_fact_identity"', source)
        self.assertGreaterEqual(source.count('"left_anti"'), 2)

    def test_publication_audit_composes_existing_and_identity_checks(self):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn("def audit_warehouse_publication(", source)
        self.assertIn("*audit_warehouse(", source)
        self.assertIn("*audit_warehouse_identity(", source)
        self.assertIn("return tuple(sorted(findings))", source)

    def test_notebook_runs_composite_audit_before_the_first_write(self):
        notebook = NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn(
            "from lakehouse_demo.warehouse_identity import",
            notebook,
        )
        self.assertIn("audit_warehouse_publication", notebook)
        self.assertIn(
            "findings = audit_warehouse_publication(",
            notebook,
        )
        self.assertLess(
            notebook.index("findings = audit_warehouse_publication("),
            notebook.index(".saveAsTable("),
        )
        self.assertNotIn("findings = audit_warehouse(", notebook)


if __name__ == "__main__":
    unittest.main()
