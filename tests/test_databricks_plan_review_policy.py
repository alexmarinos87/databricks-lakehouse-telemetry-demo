import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "governance" / "databricks_plan_review_policy.json"
BRIEF = ROOT / "docs" / "change_briefs" / "review_databricks_plan_evidence.md"
SPEC = importlib.util.spec_from_file_location(
    "review_databricks_plan_policy",
    ROOT / "scripts" / "review_databricks_plan.py",
)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


class DatabricksPlanReviewPolicyTest(unittest.TestCase):
    def test_repository_policy_loads_for_dev_and_prod(self):
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(2, document["schema_version"])
        self.assertEqual(2, document["required_plan_version"])
        self.assertEqual({"dev", "prod"}, set(document["targets"]))
        for target in ("dev", "prod"):
            with self.subTest(target=target):
                policy = review.load_policy(POLICY, target)
                self.assertFalse(policy.allow_delete)
                self.assertFalse(policy.allow_recreate)
                self.assertTrue(policy.allow_gone_delete)
                self.assertEqual(0, policy.max_delete)
                self.assertEqual(0, policy.max_recreate)

    def test_target_policies_exclude_the_opposite_namespace(self):
        dev = review.load_policy(POLICY, "dev")
        prod = review.load_policy(POLICY, "prod")
        self.assertTrue(any("prod" in item for item in dev.forbidden_fragments))
        self.assertTrue(any("dev" in item for item in prod.forbidden_fragments))

    def test_change_brief_preserves_review_and_side_effect_boundaries(self):
        brief = BRIEF.read_text(encoding="utf-8")
        for token in [
            "deterministic, sanitized accept/block decision",
            "Databricks direct-engine JSON plan",
            "databricks-plan-review.json",
            "resource-address fingerprints",
            "does not permit destructive deletes or recreation",
            "state-only cleanup",
            "still subject to human review",
            "does not invoke the Databricks CLI",
        ]:
            with self.subTest(token=token):
                self.assertIn(token, brief)


if __name__ == "__main__":
    unittest.main()
