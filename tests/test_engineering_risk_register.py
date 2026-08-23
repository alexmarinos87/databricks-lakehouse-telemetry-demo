from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "validate_engineering_risks.py"
SPEC = importlib.util.spec_from_file_location("validate_engineering_risks", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

POLICY_PATH = REPO_ROOT / "governance" / "engineering_risks.json"
MARKDOWN_PATH = REPO_ROOT / "docs" / "engineering_risk_register.md"
LOCAL_CHECKS = REPO_ROOT / "scripts" / "run_local_checks.sh"


class EngineeringRiskRegisterTest(unittest.TestCase):
    def policy_payload(self):
        return json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def write_policy(self, root: Path, payload) -> Path:
        path = root / "engineering_risks.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_current_policy_and_generated_markdown_are_valid(self):
        policy = validator.load_policy()
        validator.validate_markdown(policy)
        summary = validator.summary(policy)

        self.assertEqual("valid", summary["status"])
        self.assertEqual(17, summary["risk_count"])
        self.assertEqual(0, summary["closed_count"])
        self.assertEqual(13, summary["source_mitigated_count"])
        self.assertEqual(4, summary["source_partial_or_open_count"])
        self.assertEqual(16, summary["runtime_pending_or_blocked_count"])
        self.assertEqual(12, summary["external_pending_or_blocked_count"])

    def test_all_source_evidence_paths_are_regular_repository_files(self):
        policy = validator.load_policy()
        evidence_count = 0
        for risk in policy["risks"]:
            for evidence in risk["source_evidence"]:
                evidence_count += 1
                path = REPO_ROOT / evidence["path"]
                self.assertTrue(path.is_file(), evidence["path"])
                self.assertFalse(path.is_symlink(), evidence["path"])
        self.assertGreater(evidence_count, 50)

    def test_register_distinguishes_source_runtime_and_external_state(self):
        risks = {
            risk["id"]: risk
            for risk in validator.load_policy()["risks"]
        }

        self.assertEqual(
            ("mitigated", "pending", "not_applicable"),
            (
                risks["R-004"]["source_status"],
                risks["R-004"]["runtime_status"],
                risks["R-004"]["external_status"],
            ),
        )
        self.assertEqual("partial", risks["R-005"]["source_status"])
        self.assertEqual("partial", risks["R-006"]["source_status"])
        self.assertEqual(["PR #75"], risks["R-006"]["dependencies"])
        self.assertEqual("mitigated", risks["R-008"]["source_status"])
        self.assertEqual("partial", risks["R-009"]["source_status"])
        self.assertEqual("mitigated", risks["R-010"]["source_status"])
        self.assertEqual("mitigated", risks["R-013"]["source_status"])
        self.assertEqual("mitigated", risks["R-014"]["source_status"])
        self.assertEqual("mitigated", risks["R-015"]["source_status"])
        self.assertEqual("blocked", risks["R-016"]["external_status"])

    def test_closed_layers_cannot_retain_pending_evidence(self):
        payload = self.policy_payload()
        risk = payload["risks"][3]
        risk["source_status"] = "mitigated"
        risk["runtime_status"] = "evidenced"
        risk["external_status"] = "not_applicable"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(Path(directory), payload)
            with self.assertRaisesRegex(
                validator.RiskPolicyError, "closed but still lists pending evidence"
            ):
                validator.load_policy(path, repo_root=REPO_ROOT)

    def test_open_layers_require_pending_evidence(self):
        payload = self.policy_payload()
        payload["risks"][4]["pending_evidence"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(Path(directory), payload)
            with self.assertRaisesRegex(
                validator.RiskPolicyError, "open but has no pending evidence"
            ):
                validator.load_policy(path, repo_root=REPO_ROOT)

    def test_blocked_external_work_requires_a_durable_dependency(self):
        payload = self.policy_payload()
        payload["risks"][0]["dependencies"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(Path(directory), payload)
            with self.assertRaisesRegex(
                validator.RiskPolicyError,
                "blocked external work without a dependency",
            ):
                validator.load_policy(path, repo_root=REPO_ROOT)

    def test_unsafe_or_missing_evidence_path_fails_closed(self):
        payload = self.policy_payload()
        payload["risks"][0]["source_evidence"][0]["path"] = "../outside.txt"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(Path(directory), payload)
            with self.assertRaisesRegex(
                validator.RiskPolicyError, "source evidence path is unsafe"
            ):
                validator.load_policy(path, repo_root=REPO_ROOT)

    def test_risk_ids_must_remain_ordered_and_contiguous(self):
        payload = self.policy_payload()
        payload["risks"][1]["id"] = "R-099"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_policy(Path(directory), payload)
            with self.assertRaisesRegex(
                validator.RiskPolicyError, "ordered and contiguous"
            ):
                validator.load_policy(path, repo_root=REPO_ROOT)

    def test_markdown_drift_is_detected(self):
        policy = validator.load_policy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "register.md"
            path.write_text(
                validator.render_markdown(policy) + "stale\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                validator.RiskPolicyError, "Markdown is stale"
            ):
                validator.validate_markdown(policy, path)

    def test_generated_register_keeps_local_links_and_closure_boundary(self):
        rendered = validator.render_markdown(validator.load_policy())

        self.assertGreater(rendered.count("](../"), 50)
        self.assertIn("Repository-source mitigation is not runtime closure", rendered)
        self.assertIn("Agent confidence is not closure", rendered)
        self.assertIn("source=partial; runtime=pending", rendered)
        self.assertIn("source=mitigated; runtime=blocked", rendered)

    def test_local_checks_execute_the_risk_validator_without_mutating_docs(self):
        checks = LOCAL_CHECKS.read_text(encoding="utf-8")

        self.assertIn("scripts/validate_engineering_risks.py", checks)
        self.assertNotIn("validate_engineering_risks.py --write", checks)


if __name__ == "__main__":
    unittest.main()
