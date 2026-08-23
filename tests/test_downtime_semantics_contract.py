from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = REPO_ROOT / "governance" / "downtime_semantics.json"
MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_downtime_semantics.py"
PIPELINE = REPO_ROOT / "src" / "lakehouse_demo" / "downtime_pipeline.py"
QUALITY = REPO_ROOT / "src" / "lakehouse_demo" / "spark_quality.py"
WAREHOUSE_IDENTITY = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_identity.py"
WAREHOUSE_MEASURES = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_measures.py"
WAREHOUSE_PUBLICATION = REPO_ROOT / "src" / "lakehouse_demo" / "warehouse_publication.py"
GOLD_NOTEBOOK = REPO_ROOT / "notebooks" / "03_gold_models.py"
EXPECTATIONS = REPO_ROOT / "notebooks" / "06_lakeflow_quality_expectations.py"
WAREHOUSE_NOTEBOOK = REPO_ROOT / "notebooks" / "07_warehouse_model.py"
REPORTING_ASSET = REPO_ROOT / "sql" / "reporting_assets" / "daily_uptime_by_site_model.sql"
REPORTING_SQL = REPO_ROOT / "sql" / "gold_reporting_queries.sql"
DOCUMENTATION = REPO_ROOT / "docs" / "downtime_semantics.md"
CHANGE_BRIEF = REPO_ROOT / "docs" / "change_briefs" / "propagate_downtime_semantics.md"


class DowntimeSemanticsContractTest(unittest.TestCase):
    def test_policy_defines_independent_attributed_downtime(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        self.assertEqual(1, policy["schema_version"])
        self.assertEqual("attributed_incident_v1", policy["semantic_version"])
        self.assertTrue(
            policy["relationships"]["attributed_downtime_may_exceed_observed"]
        )
        self.assertTrue(policy["relationships"]["downtime_load_may_exceed_100"])
        self.assertEqual(
            "uptime_pct", policy["consumer_guidance"]["availability_measure"]
        )
        self.assertEqual(
            "downtime_load_pct",
            policy["consumer_guidance"]["downtime_workload_measure"],
        )
        self.assertIn("pending_external_business_signoff", policy["decision_state"])

    def test_executable_contract_does_not_cap_attributed_downtime(self):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn('SEMANTIC_VERSION = "attributed_incident_v1"', source)
        self.assertIn('withColumn("downtime_load_pct"', source)
        self.assertIn('withColumn(\n            "downtime_exceeds_observed"', source)
        self.assertIn("status_minutes_exceed_observed", source)
        self.assertIn("legacy_downtime_pct_formula_mismatch", source)
        self.assertIn("There is intentionally no finding", source)
        self.assertNotIn("F.least", source)

    def test_governed_pipeline_materializes_and_audits_both_layers(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")
        identity = WAREHOUSE_IDENTITY.read_text(encoding="utf-8")
        measures = WAREHOUSE_MEASURES.read_text(encoding="utf-8")
        publication = WAREHOUSE_PUBLICATION.read_text(encoding="utf-8")

        for token in (
            "build_governed_gold_frames",
            "build_governed_warehouse_frames",
            "ensure_materialized_downtime_semantics",
            "materialized_downtime_findings",
            "audit_warehouse_downtime_semantics",
            "downtime_load_formula_mismatch",
            "downtime_compatibility_alias_mismatch",
            "downtime_exceedance_flag_mismatch",
            "downtime_semantics_version_mismatch",
        ):
            self.assertIn(token, pipeline)
        for token in (
            "downtime_load_pct",
            "downtime_exceeds_observed",
            "downtime_semantics_version",
        ):
            self.assertIn(token, identity)
            self.assertIn(token, measures)
        self.assertIn("audit_warehouse_downtime_semantics", publication)

    def test_notebooks_use_the_governed_wrappers(self):
        gold = GOLD_NOTEBOOK.read_text(encoding="utf-8")
        warehouse = WAREHOUSE_NOTEBOOK.read_text(encoding="utf-8")

        self.assertIn("build_governed_gold_frames", gold)
        self.assertIn("gold_frames = build_governed_gold_frames(silver)", gold)
        self.assertIn("build_governed_warehouse_frames", warehouse)
        self.assertIn(
            "warehouse_frames = build_governed_warehouse_frames(gold_uptime, gold_failures)",
            warehouse,
        )

    def test_quality_replaces_the_obsolete_high_load_warning(self):
        quality = QUALITY.read_text(encoding="utf-8")

        self.assertIn("uptime_fact_downtime_semantics_valid", quality)
        self.assertIn("materialized_downtime_findings", quality)
        self.assertNotIn("uptime_fact_downtime_semantics_review", quality)
        self.assertNotIn("approved downtime business definition", quality)

    def test_lakeflow_and_reporting_use_explicit_semantic_evidence(self):
        expectations = EXPECTATIONS.read_text(encoding="utf-8")
        reporting_asset = REPORTING_ASSET.read_text(encoding="utf-8")
        reporting_sql = REPORTING_SQL.read_text(encoding="utf-8")

        for token in (
            "downtime_load_formula_valid",
            "downtime_compatibility_alias_consistent",
            "downtime_exceedance_flag_consistent",
            "downtime_semantics_version_known",
            "attributed_incident_v1",
        ):
            self.assertIn(token, expectations)
        for source in (reporting_asset, reporting_sql):
            self.assertIn("avg_downtime_load_pct", source)
            self.assertIn("attributed_downtime_minutes", source)
            self.assertIn("downtime_semantics_version", source)
        self.assertNotIn("downtime_load_pct <= 100", expectations)
        self.assertNotIn("downtime_load_pct BETWEEN 0 AND 100", expectations)

    def test_documentation_separates_availability_from_downtime_load(self):
        documentation = DOCUMENTATION.read_text(encoding="utf-8")
        normalized_documentation = " ".join(documentation.split())

        self.assertIn("Use `uptime_pct` for availability", documentation)
        self.assertIn("It may exceed 100", documentation)
        self.assertIn("60 | 120 | 200%", documentation)
        self.assertIn("no observation denominator", documentation)
        self.assertIn(
            "must not be represented as approval",
            normalized_documentation,
        )
        self.assertIn("compatibility alias", documentation)
        self.assertIn("Repository integration", documentation)
        self.assertIn("not Databricks runtime evidence", documentation)

    def test_policy_and_documentation_use_the_same_tolerance(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        documentation = DOCUMENTATION.read_text(encoding="utf-8")
        module = MODULE.read_text(encoding="utf-8")

        self.assertEqual(
            0.01,
            policy["relationships"]["formula_tolerance_percentage_points"],
        )
        self.assertIn("0.01 percentage points", documentation)
        self.assertIn("FORMULA_TOLERANCE_PERCENTAGE_POINTS = 0.01", module)

    def test_change_brief_records_compatibility_and_runtime_boundaries(self):
        brief = CHANGE_BRIEF.read_text(encoding="utf-8")

        self.assertIn("policy/runtime split", brief)
        self.assertIn("compatibility alias", brief)
        self.assertIn("does not remove", brief)
        self.assertIn("does not deploy", brief)
        self.assertIn("authenticated development-runtime evidence", brief)


if __name__ == "__main__":
    unittest.main()
