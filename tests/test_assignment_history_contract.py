from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = REPO_ROOT / "governance" / "warehouse_assignment_policy.json"
MODULE = REPO_ROOT / "src" / "lakehouse_demo" / "spark_assignment_history.py"
DOCUMENTATION = REPO_ROOT / "docs" / "warehouse_assignment_semantics.md"


class AssignmentHistoryContractTest(unittest.TestCase):
    def test_policy_prohibits_unknown_surrogate_members(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        self.assertEqual(1, policy["schema_version"])
        self.assertEqual(
            "effective_dated_assignment_v1", policy["semantic_version"]
        )
        self.assertEqual(
            "block_publication",
            policy["unknown_member_policy"]["trusted_fact_behavior"],
        )
        self.assertEqual(
            "prohibited",
            policy["unknown_member_policy"]["surrogate_key_placeholder"],
        )
        self.assertEqual(
            "none; uptime and failure evidence have equal authority and disagreement blocks publication",
            policy["assignment_policy"]["source_precedence"],
        )

    def test_module_builds_effective_ranges_without_conflict_winner(self):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn('SEMANTIC_VERSION = "effective_dated_assignment_v1"', source)
        self.assertIn("same_day_assignment_conflict", source)
        self.assertIn("left_anti", source)
        self.assertIn("F.date_sub(F.lead", source)
        self.assertIn("assignment_history_range_overlap", source)
        self.assertIn("assignment_unresolved", source)
        self.assertIn("assignment_ambiguous", source)
        self.assertIn("assignment_resolution_count_mismatch", source)
        self.assertNotIn("UNKNOWN", source)
        self.assertNotIn('F.lit(-1)', source)
        self.assertNotIn("first(", source)

    def test_documentation_requires_rebuild_for_late_assignments(self):
        documentation = DOCUMENTATION.read_text(encoding="utf-8")

        for phrase in (
            "Trusted warehouse facts do not use a fabricated `Unknown`",
            "same-day conflict",
            "Uptime and failure evidence have equal authority",
            "Rebuild the machine's complete history",
            "Do not patch only the latest fact",
            "Do not represent this repository-only contract as a completed live warehouse migration",
        ):
            self.assertIn(phrase, documentation)

    def test_policy_lists_publication_evidence_families(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        required = set(policy["required_evidence"])

        self.assertIn("assignment_history_grain_unique", required)
        self.assertIn("assignment_ranges_non_overlapping", required)
        self.assertIn("fact_dimension_keys_resolve", required)
        self.assertIn("source_to_fact_count_reconciliation", required)
        self.assertIn("natural_identity_reconciliation", required)


if __name__ == "__main__":
    unittest.main()
