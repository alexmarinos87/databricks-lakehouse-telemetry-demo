from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = REPO_ROOT / "governance" / "downtime_semantics.json"
MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_downtime_semantics.py"
DOCUMENTATION = REPO_ROOT / "docs" / "downtime_semantics.md"


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


if __name__ == "__main__":
    unittest.main()
