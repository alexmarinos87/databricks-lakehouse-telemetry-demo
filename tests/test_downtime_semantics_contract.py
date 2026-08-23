import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "downtime_semantics.json"
SEMANTICS = ROOT / "src" / "lakehouse_demo" / "downtime_semantics.py"
WAREHOUSE = ROOT / "src" / "lakehouse_demo" / "spark_warehouse.py"
QUALITY = ROOT / "src" / "lakehouse_demo" / "spark_quality.py"
MEASURES = ROOT / "src" / "lakehouse_demo" / "warehouse_measures.py"
IDENTITY = ROOT / "src" / "lakehouse_demo" / "warehouse_identity.py"
CHANGE_BRIEF = ROOT / "docs" / "change_briefs" / "downtime_impact_semantics.md"


class DowntimeSemanticsContractTest(unittest.TestCase):
    def test_machine_readable_contract_approves_uncapped_impact_semantics(self):
        contract = json.loads(CONFIG.read_text(encoding="utf-8"))

        self.assertEqual(1, contract["schema_version"])
        downtime = contract["source_fields"]["downtime_minutes"]
        self.assertIn("outage-impact", downtime["definition"])
        self.assertIn("may exceed duration_minutes", downtime["relationship"])
        ratio = contract["derived_fields"]["downtime_impact_ratio_pct"]
        self.assertEqual(0, ratio["minimum"])
        self.assertIsNone(ratio["maximum"])
        self.assertIn("not availability", ratio["definition"])
        self.assertIn(
            "downtime_impact_ratio_pct is capped at 100",
            contract["prohibited_interpretations"],
        )

    def test_shared_formula_is_uncapped_and_null_without_positive_observation(self):
        source = SEMANTICS.read_text(encoding="utf-8")

        self.assertIn('IMPACT_RATIO_COLUMN = "downtime_impact_ratio_pct"', source)
        self.assertIn("F.col(observed_column) > 0", source)
        self.assertIn("F.round(", source)
        self.assertNotIn("F.least", source)
        self.assertNotIn("100.0)", source)
        self.assertIn("otherwise(F.lit(None).cast", source)

    def test_warehouse_and_measure_layers_emit_one_semantic_name(self):
        for path in (WAREHOUSE, MEASURES, IDENTITY):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("downtime_impact_ratio_pct", source)
                self.assertNotIn('"downtime_pct"', source)
        self.assertIn("downtime_impact_ratio()", WAREHOUSE.read_text(encoding="utf-8"))
        self.assertIn("downtime_impact_ratio()", MEASURES.read_text(encoding="utf-8"))

    def test_quality_delegates_to_the_shared_semantic_validator(self):
        source = QUALITY.read_text(encoding="utf-8")

        self.assertIn(
            "from lakehouse_demo.downtime_semantics import invalid_downtime_impact_rows",
            source,
        )
        self.assertIn("invalid_downtime_impact_rows(uptime).count()", source)
        self.assertNotIn('"downtime_pct"', source)

    def test_unresolved_warning_is_removed_in_favour_of_error_consistency(self):
        source = QUALITY.read_text(encoding="utf-8")

        self.assertNotIn("uptime_fact_downtime_semantics_review", source)
        self.assertIn("uptime_fact_downtime_impact_consistent", source)
        self.assertIn("approved uncapped formula", source)

    def test_change_brief_records_schema_migration_and_rollback(self):
        source = CHANGE_BRIEF.read_text(encoding="utf-8")

        for token in (
            "may exceed",
            "downtime_impact_ratio_pct",
            "not capped at 100",
            "intentional warehouse schema rename",
            "restore the previous uptime fact Delta",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
