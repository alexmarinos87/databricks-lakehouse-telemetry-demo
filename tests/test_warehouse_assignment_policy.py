import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPARK_WAREHOUSE = ROOT / "src" / "lakehouse_demo" / "spark_warehouse.py"
PURE_CONTRACT = ROOT / "src" / "lakehouse_demo" / "warehouse_contracts.py"
CHANGE_BRIEF = ROOT / "docs" / "change_briefs" / "versioned_machine_assignments.md"


class WarehouseAssignmentPolicyTest(unittest.TestCase):
    def test_required_identity_is_fail_closed_without_unknown_sentinels(self):
        source = SPARK_WAREHOUSE.read_text(encoding="utf-8")

        self.assertIn(
            'UNKNOWN_MEMBER_POLICY = "reject_required_business_identity"',
            source,
        )
        self.assertIn("def _require_business_identity(", source)
        self.assertIn("missing required business identity", source)
        self.assertNotIn('F.lit("UNKNOWN")', source)
        self.assertNotIn('F.lit(0).alias("machine_key")', source)

    def test_machine_dimension_is_versioned_by_dated_assignment(self):
        source = SPARK_WAREHOUSE.read_text(encoding="utf-8")

        for token in (
            "assignment_version",
            "valid_from_date",
            "valid_to_date",
            "is_current",
            'F.xxhash64("machine_id", "valid_from_date")',
            "conflicting same-day machine assignments",
        ):
            self.assertIn(token, source)

    def test_fact_construction_avoids_dimension_inner_joins(self):
        source = SPARK_WAREHOUSE.read_text(encoding="utf-8")
        build_section = source[
            source.index("def build_warehouse_frames(") :
            source.index("def _duplicate_count(")
        ]

        self.assertIn("_resolve_machine_version(", build_section)
        self.assertIn("machine assignment resolution changed row count", source)
        self.assertNotIn('"inner"', build_section)
        self.assertIn('F.xxhash64("client_id")', build_section)
        self.assertIn('F.xxhash64("client_id", "site_id")', build_section)
        self.assertIn('F.xxhash64("model")', build_section)

    def test_pure_contract_scopes_assignment_conflicts_to_machine_and_date(self):
        source = PURE_CONTRACT.read_text(encoding="utf-8")

        self.assertIn(
            '_MACHINE_ASSIGNMENT_GRAIN = ("machine_id", "event_date")',
            source,
        )
        self.assertIn("a change on a\n    later date is a versioned assignment", source)

    def test_change_brief_records_compatibility_and_rollback(self):
        source = CHANGE_BRIEF.read_text(encoding="utf-8")

        for token in (
            "does not invent",
            "same `event_date`",
            "No-silent-loss boundary",
            "one row per dated assignment",
            "restore the previous",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
