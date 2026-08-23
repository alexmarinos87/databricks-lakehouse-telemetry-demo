from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "validate_runtime_compatibility.py"
SPEC = importlib.util.spec_from_file_location("validate_runtime_compatibility", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

POLICY = REPO_ROOT / "governance" / "runtime_compatibility.json"
DOCUMENTATION = REPO_ROOT / "docs" / "runtime_compatibility.md"


class RuntimeCompatibilityTest(unittest.TestCase):
    def test_repository_matches_the_complete_active_baseline(self):
        policy = validator.load_policy(POLICY)
        result = validator.validate_repository(policy)

        self.assertEqual("valid", result["status"])
        self.assertEqual("3.11", result["baseline"]["python"])
        self.assertEqual("17", result["baseline"]["java"])
        self.assertEqual("3.5.0", result["baseline"]["pyspark"])
        self.assertEqual("0.10.9.7", result["baseline"]["py4j"])
        self.assertEqual("15.4.x-scala2.12", result["baseline"]["databricks_runtime"])
        self.assertEqual(64, len(result["baseline_fingerprint"]))
        self.assertGreaterEqual(result["blocked_candidate_count"], 2)

    def test_every_baseline_evidence_file_is_hashed(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        result = validator.validate_repository(policy)

        self.assertEqual(
            set(policy["active_baseline"]["evidence"]),
            set(result["evidence_hashes"]),
        )
        for digest in result["evidence_hashes"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_major_upgrade_candidates_remain_blocked_and_complete(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        candidate_ids = {candidate["id"] for candidate in policy["upgrade_candidates"]}
        self.assertIn("python-3.14", candidate_ids)
        self.assertIn("spark-4.2", candidate_ids)
        for candidate in policy["upgrade_candidates"]:
            with self.subTest(candidate=candidate["id"]):
                self.assertEqual(
                    "blocked_pending_complete_matrix", candidate["status"]
                )
                self.assertIn("all_standard_tests", candidate["required_evidence"])
                self.assertIn("all_spark_runtime_tests", candidate["required_evidence"])
                self.assertIn("rollback_image_digest", candidate["required_evidence"])

    def test_policy_rejects_an_unblocked_candidate_without_evidence(self):
        payload = json.loads(POLICY.read_text(encoding="utf-8"))
        payload["upgrade_candidates"][0]["status"] = "accepted"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compatibility.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "remain blocked"):
                validator.load_policy(path)

    def test_spark_dependencies_are_exact_and_hashed(self):
        requirements = (REPO_ROOT / "requirements-spark.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("py4j==0.10.9.7", requirements)
        self.assertIn("pyspark==3.5.0", requirements)
        self.assertGreaterEqual(requirements.count("--hash=sha256:"), 2)
        self.assertNotIn(">=", requirements)
        self.assertNotIn("~=", requirements)

    def test_documentation_requires_dev_runtime_and_performance_evidence(self):
        documentation = DOCUMENTATION.read_text(encoding="utf-8")

        for phrase in (
            "complete matrix",
            "controlled development runtime execution",
            "Performance evidence",
            "rollback image digest",
            "Do not merge a standalone Py4J bump",
            "vendor support matrices",
        ):
            self.assertIn(phrase, documentation)


if __name__ == "__main__":
    unittest.main()
