from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_identity_privilege_evidence",
    ROOT / "scripts" / "verify_identity_privilege_evidence.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def fingerprint(seed: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


class VerifyIdentityPrivilegeEvidenceTest(unittest.TestCase):
    def make_manifest(
        self,
        *,
        captured_at: datetime = NOW - timedelta(hours=1),
        deployment_fingerprint: str | None = None,
        runtime_fingerprint: str | None = None,
    ) -> dict:
        timestamp = captured_at.isoformat().replace("+00:00", "Z")
        observations = []
        for evidence_id, rule in m.REQUIRED_EVIDENCE_RULES.items():
            outcome = "succeeded" if rule["expectation"] == "allowed" else "denied"
            observations.append(
                {
                    "evidence_id": evidence_id,
                    "identity": rule["identity"],
                    "capabilities": list(rule["capabilities"]),
                    "expectation": rule["expectation"],
                    "outcome": outcome,
                    "method": sorted(rule["methods"])[0],
                    "observed_at_utc": timestamp,
                    "evidence_sha256": fingerprint(evidence_id),
                }
            )
        return {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": timestamp,
            "workspace_fingerprint": fingerprint("workspace"),
            "identities": {
                "deployment": {
                    "principal_fingerprint": deployment_fingerprint
                    or fingerprint("deployment")
                },
                "runtime": {
                    "principal_fingerprint": runtime_fingerprint
                    or fingerprint("runtime")
                },
            },
            "observations": observations,
        }

    def write_inputs(self, root: Path, manifest: dict) -> tuple[Path, Path]:
        contract = ROOT / "config" / "identity_privilege_contract.json"
        evidence = root / "identity-evidence.json"
        evidence.write_text(json.dumps(manifest), encoding="utf-8")
        return contract, evidence

    def test_complete_development_evidence_is_verified_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract, evidence = self.write_inputs(root, self.make_manifest())
            report = m.verify_evidence(contract, evidence, now=NOW)
            m.write_outputs(root / "output", report)
            stored = json.loads(
                (root / "output" / m.OUTPUT_JSON).read_text(encoding="utf-8")
            )
            markdown = (root / "output" / m.OUTPUT_MARKDOWN).read_text(
                encoding="utf-8"
            )

        self.assertEqual("verified", stored["status"])
        self.assertEqual(5, stored["required_evidence"]["verified"])
        self.assertEqual([], stored["findings"])
        self.assertEqual(m.render_markdown(stored), markdown)
        serialized = json.dumps(stored)
        self.assertNotIn("lakehouse-demo-ci", serialized)
        self.assertNotIn("lakehouse-demo-runtime", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("provider_response", serialized)

    def test_missing_required_evidence_blocks(self):
        manifest = self.make_manifest()
        manifest["observations"].pop()
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            report = m.verify_evidence(contract, evidence, now=NOW)
        self.assertEqual("blocked", report["status"])
        self.assertIn(
            "required_evidence_missing",
            {finding["category"] for finding in report["findings"]},
        )

    def test_allowed_failure_and_denial_success_block(self):
        manifest = self.make_manifest()
        by_id = {
            observation["evidence_id"]: observation
            for observation in manifest["observations"]
        }
        by_id["runtime_principal_can_execute_job_and_pipeline"]["outcome"] = "denied"
        by_id["runtime_principal_cannot_deploy_bundle"]["outcome"] = "succeeded"
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            report = m.verify_evidence(contract, evidence, now=NOW)
        categories = {finding["category"] for finding in report["findings"]}
        self.assertIn("required_capability_not_succeeded", categories)
        self.assertIn("expected_denial_not_observed", categories)

    def test_required_method_and_capability_contracts_fail_closed(self):
        manifest = self.make_manifest()
        observation = next(
            item
            for item in manifest["observations"]
            if item["evidence_id"]
            == "deployment_principal_cannot_select_curated_tables"
        )
        observation["method"] = "permission_readback"
        observation["capabilities"] = ["modify_lakehouse_tables"]
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            report = m.verify_evidence(contract, evidence, now=NOW)
        categories = {finding["category"] for finding in report["findings"]}
        self.assertIn("required_evidence_contract_mismatch", categories)

    def test_overlapping_identity_fingerprints_block(self):
        same = fingerprint("same")
        manifest = self.make_manifest(
            deployment_fingerprint=same, runtime_fingerprint=same
        )
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            report = m.verify_evidence(contract, evidence, now=NOW)
        self.assertIn(
            "identity_fingerprints_overlap",
            {finding["category"] for finding in report["findings"]},
        )
        self.assertEqual(0, report["required_evidence"]["verified"])

    def test_stale_and_future_evidence_block(self):
        for captured_at, expected in (
            (NOW - timedelta(hours=73), "evidence_capture_is_stale"),
            (NOW + timedelta(minutes=6), "evidence_capture_is_in_future"),
        ):
            with self.subTest(expected=expected):
                manifest = self.make_manifest(captured_at=captured_at)
                with tempfile.TemporaryDirectory() as directory:
                    contract, evidence = self.write_inputs(Path(directory), manifest)
                    report = m.verify_evidence(contract, evidence, now=NOW)
                categories = {finding["category"] for finding in report["findings"]}
                self.assertIn(expected, categories)
                self.assertEqual(0, report["required_evidence"]["verified"])

    def test_unknown_capability_and_extra_field_fail_closed(self):
        manifest = self.make_manifest()
        manifest["observations"][0]["capabilities"] = ["account_admin"]
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            report = m.verify_evidence(contract, evidence, now=NOW)
        self.assertIn(
            "observation_capability_expectation_mismatch",
            {finding["category"] for finding in report["findings"]},
        )

        manifest = self.make_manifest()
        manifest["raw_provider_response"] = "forbidden"
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            with self.assertRaisesRegex(m.VerificationError, "evidence_shape_invalid"):
                m.verify_evidence(contract, evidence, now=NOW)

    def test_duplicate_observation_and_non_dev_target_are_invalid(self):
        manifest = self.make_manifest()
        manifest["observations"].append(dict(manifest["observations"][0]))
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            with self.assertRaisesRegex(
                m.VerificationError, "observation_evidence_id_duplicate"
            ):
                m.verify_evidence(contract, evidence, now=NOW)

        manifest = self.make_manifest()
        manifest["target"] = "prod"
        with tempfile.TemporaryDirectory() as directory:
            contract, evidence = self.write_inputs(Path(directory), manifest)
            with self.assertRaisesRegex(
                m.VerificationError, "evidence_target_must_be_dev"
            ):
                m.verify_evidence(contract, evidence, now=NOW)

    def test_output_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract, evidence = self.write_inputs(root, self.make_manifest())
            report = m.verify_evidence(contract, evidence, now=NOW)
            target = root / "target"
            target.mkdir()
            link = root / "output"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                m.VerificationError, "output_directory_is_symlink"
            ):
                m.write_outputs(link, report)


if __name__ == "__main__":
    unittest.main()
