from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_external_control_evidence_index",
    ROOT / "scripts" / "build_external_control_evidence_index.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

POLICY = ROOT / "governance" / "external_control_evidence_policy.json"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
COMMIT = "a" * 40


def sha(value: bytes | str) -> str:
    payload = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class BuildExternalControlEvidenceIndexTest(unittest.TestCase):
    def inputs(
        self,
        root: Path,
        *,
        generated_times: tuple[datetime, datetime, datetime] | None = None,
    ) -> tuple[Path, Path, Path, dict[str, dict]]:
        evidence_root = root / "evidence"
        repository_root = root / "repository"
        (repository_root / "scripts").mkdir(parents=True)
        evidence_root.mkdir()
        times = generated_times or (
            NOW - timedelta(minutes=40),
            NOW - timedelta(minutes=30),
            NOW - timedelta(minutes=20),
        )
        verifier_paths = {
            "github_governance": "scripts/verify_github_governance.py",
            "databricks_federation": "scripts/verify_databricks_federation.py",
            "identity_privilege": "scripts/verify_identity_privilege_evidence.py",
        }
        for control_id, relative in verifier_paths.items():
            (repository_root / relative).write_text(
                f"# current verifier for {control_id}\n",
                encoding="utf-8",
            )

        github_report = {
            "schema_version": 1,
            "status": "verified",
            "generated_at_utc": utc(times[0]),
            "repository": REPOSITORY,
            "branch": "main",
            "branch_head_sha": COMMIT,
            "main_protected": True,
            "branch_protection": {
                "required_checks_strict": True,
                "validate_required": True,
                "administrator_enforcement": True,
                "linear_history": True,
                "force_pushes_blocked": True,
                "deletion_blocked": True,
                "conversation_resolution": True,
                "dismiss_stale_reviews": True,
            },
            "environments": [
                {
                    "environment": environment,
                    "verified": True,
                    "custom_main_only_policy": True,
                    "variables": {
                        "DATABRICKS_HOST": True,
                        "DATABRICKS_CLIENT_ID": True,
                        "DATABRICKS_RUNTIME_CLIENT_ID": True,
                    },
                    "static_client_secret_absent": True,
                }
                for environment in ("dev-plan", "prod-plan", "dev", "prod")
            ],
            "findings": [],
        }
        federation_report = {
            "schema_version": 1,
            "status": "verified",
            "generated_at_utc": utc(times[1]),
            "repository": REPOSITORY,
            "issuer": "https://token.actions.githubusercontent.com",
            "principals": [
                {
                    "numeric_id_matches": True,
                    "application_id_matches": True,
                    "active": True,
                    "account_admin_absent": True,
                    "oauth_secrets_absent": True,
                    "policies": [
                        {
                            "environment": "dev",
                            "role": "deployment",
                            "exact_policy": True,
                        }
                    ],
                },
                {
                    "numeric_id_matches": True,
                    "application_id_matches": True,
                    "active": True,
                    "account_admin_absent": True,
                    "oauth_secrets_absent": True,
                    "policies": [
                        {
                            "environment": "dev",
                            "role": "runtime",
                            "exact_policy": True,
                        }
                    ],
                },
            ],
            "findings": [],
        }
        identity_report = {
            "schema_version": 1,
            "status": "verified",
            "generated_at_utc": utc(times[2]),
            "target": "dev",
            "repository": REPOSITORY,
            "source_commit": COMMIT,
            "captured_at_utc": utc(times[2] - timedelta(minutes=1)),
            "required_evidence": {"required": 5, "observed": 5, "verified": 5},
            "identity_fingerprints": {
                "deployment": sha("deployment"),
                "runtime": sha("runtime"),
            },
            "findings": [],
        }
        reports = {
            "github_governance": github_report,
            "databricks_federation": federation_report,
            "identity_privilege": identity_report,
        }
        controls = []
        for index, control_id in enumerate(m.EXPECTED_CONTROLS):
            report_path = f"{control_id}/verification.json"
            absolute = evidence_root / report_path
            absolute.parent.mkdir()
            payload = json.dumps(reports[control_id], sort_keys=True).encode()
            absolute.write_bytes(payload)
            verifier_path = repository_root / verifier_paths[control_id]
            controls.append(
                {
                    "control_id": control_id,
                    "report_path": report_path,
                    "expected_report_sha256": sha(payload),
                    "expected_verifier_sha256": sha(verifier_path.read_bytes()),
                    "workflow_run_fingerprint": sha(f"workflow-{index}"),
                }
            )
        metadata = {
            "schema_version": 1,
            "target": "dev",
            "repository": REPOSITORY,
            "source_commit": COMMIT,
            "captured_at_utc": utc(NOW - timedelta(minutes=5)),
            "controls": controls,
        }
        metadata_path = root / "metadata.json"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return metadata_path, evidence_root, repository_root, reports

    def build(self, root: Path, **kwargs) -> dict:
        metadata, evidence, repository, _ = self.inputs(root, **kwargs)
        return m.build_index(POLICY, metadata, evidence, repository, now=NOW)

    def rewrite_report(
        self,
        metadata_path: Path,
        evidence_root: Path,
        control_index: int,
        report: dict,
    ) -> None:
        metadata = json.loads(metadata_path.read_text())
        descriptor = metadata["controls"][control_index]
        payload = json.dumps(report, sort_keys=True).encode()
        (evidence_root / descriptor["report_path"]).write_bytes(payload)
        descriptor["expected_report_sha256"] = sha(payload)
        metadata_path.write_text(json.dumps(metadata))

    def test_verified_reports_produce_sanitized_exact_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, evidence, repository, _ = self.inputs(root)
            index = m.build_index(POLICY, metadata, evidence, repository, now=NOW)
            m.write_outputs(root / "output", index)
            stored = json.loads((root / "output" / m.OUTPUT_JSON).read_text())
            markdown = (root / "output" / m.OUTPUT_MARKDOWN).read_text()
        self.assertEqual("verified", stored["status"])
        self.assertFalse(stored["external_mutation_authorized"])
        self.assertEqual(
            list(m.EXPECTED_CONTROLS),
            [item["control_id"] for item in stored["controls"]],
        )
        self.assertEqual([], stored["findings"])
        self.assertEqual(m.render_markdown(stored), markdown)
        serialized = json.dumps(stored)
        for forbidden in (
            "verification.json",
            "scripts/verify_",
            "workspace_url",
            "client_id",
            "provider_response",
            "token",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_unverified_findings_and_effective_control_drift_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, evidence, repository, reports = self.inputs(root)
            reports["github_governance"]["main_protected"] = False
            reports["databricks_federation"]["principals"][0][
                "oauth_secrets_absent"
            ] = False
            reports["identity_privilege"]["required_evidence"]["verified"] = 4
            for index, control_id in enumerate(m.EXPECTED_CONTROLS):
                self.rewrite_report(metadata, evidence, index, reports[control_id])
            result = m.build_index(POLICY, metadata, evidence, repository, now=NOW)
        self.assertEqual("blocked", result["status"])
        categories = {item["category"] for item in result["findings"]}
        self.assertIn("github_report_main_not_protected", categories)
        self.assertIn("federation_report_principal_not_verified", categories)
        self.assertIn("identity_report_required_evidence_incomplete", categories)

    def test_source_commit_repository_status_and_findings_are_bound(self):
        mutations = (
            (
                "github_governance",
                "branch_head_sha",
                "b" * 40,
                "github_report_source_commit_mismatch",
            ),
            (
                "identity_privilege",
                "source_commit",
                "b" * 40,
                "identity_report_source_commit_mismatch",
            ),
            (
                "databricks_federation",
                "repository",
                "other/repository",
                "control_report_repository_mismatch",
            ),
            (
                "databricks_federation",
                "status",
                "blocked",
                "control_report_is_not_verified",
            ),
            (
                "identity_privilege",
                "findings",
                [{"category": "drift"}],
                "control_report_contains_findings",
            ),
        )
        for control_id, key, value, expected in mutations:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                metadata, evidence, repository, reports = self.inputs(root)
                reports[control_id][key] = value
                index = list(m.EXPECTED_CONTROLS).index(control_id)
                self.rewrite_report(metadata, evidence, index, reports[control_id])
                result = m.build_index(
                    POLICY, metadata, evidence, repository, now=NOW
                )
                categories = {item["category"] for item in result["findings"]}
                self.assertIn(expected, categories)

    def test_stale_future_after_capture_and_spread_block(self):
        cases = (
            (
                (
                    NOW - timedelta(hours=73),
                    NOW - timedelta(minutes=30),
                    NOW - timedelta(minutes=20),
                ),
                "control_report_is_stale",
            ),
            (
                (
                    NOW + timedelta(minutes=6),
                    NOW - timedelta(minutes=30),
                    NOW - timedelta(minutes=20),
                ),
                "control_report_is_in_future",
            ),
            (
                (
                    NOW - timedelta(hours=5),
                    NOW - timedelta(minutes=30),
                    NOW - timedelta(minutes=20),
                ),
                "external_control_capture_spread_exceeds_policy",
            ),
        )
        for times, expected in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                result = self.build(Path(directory), generated_times=times)
                self.assertIn(
                    expected,
                    {item["category"] for item in result["findings"]},
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, evidence, repository, reports = self.inputs(root)
            reports["identity_privilege"]["generated_at_utc"] = utc(
                NOW + timedelta(minutes=1)
            )
            self.rewrite_report(
                metadata, evidence, 2, reports["identity_privilege"]
            )
            document = json.loads(metadata.read_text())
            document["captured_at_utc"] = utc(NOW - timedelta(minutes=10))
            metadata.write_text(json.dumps(document))
            result = m.build_index(POLICY, metadata, evidence, repository, now=NOW)
        self.assertIn(
            "control_report_after_index_capture",
            {item["category"] for item in result["findings"]},
        )

    def test_report_and_current_verifier_digests_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, evidence, repository, _ = self.inputs(root)
            document = json.loads(metadata.read_text())
            document["controls"][0]["expected_report_sha256"] = sha("wrong")
            metadata.write_text(json.dumps(document))
            with self.assertRaisesRegex(
                m.IndexError, "external_control_report_digest_mismatch"
            ):
                m.build_index(POLICY, metadata, evidence, repository, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, evidence, repository, _ = self.inputs(root)
            (repository / "scripts" / "verify_github_governance.py").write_text(
                "# drifted verifier\n"
            )
            with self.assertRaisesRegex(
                m.IndexError, "external_control_verifier_digest_mismatch"
            ):
                m.build_index(POLICY, metadata, evidence, repository, now=NOW)

    def test_duplicate_order_path_and_digest_metadata_fail_closed(self):
        mutations = (
            (
                "external_control_metadata_control_order_invalid",
                lambda document: document["controls"].reverse(),
            ),
            (
                "external_control_report_path_duplicate",
                lambda document: document["controls"][1].update(
                    report_path=document["controls"][0]["report_path"]
                ),
            ),
            (
                "external_control_report_digest_duplicate",
                lambda document: document["controls"][1].update(
                    expected_report_sha256=document["controls"][0][
                        "expected_report_sha256"
                    ]
                ),
            ),
            (
                "external_control_report_path_not_canonical",
                lambda document: document["controls"][0].update(
                    report_path="github_governance/./verification.json"
                ),
            ),
        )
        for category, mutate in mutations:
            with (
                self.subTest(category=category),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                metadata, evidence, repository, _ = self.inputs(root)
                document = json.loads(metadata.read_text())
                mutate(document)
                metadata.write_text(json.dumps(document))
                with self.assertRaisesRegex(m.IndexError, category):
                    m.build_index(
                        POLICY, metadata, evidence, repository, now=NOW
                    )

    def test_symlink_inputs_and_output_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, evidence, repository, _ = self.inputs(root)
            document = json.loads(metadata.read_text())
            path = evidence / document["controls"][0]["report_path"]
            target = path.with_name("target.json")
            path.rename(target)
            path.symlink_to(target)
            with self.assertRaisesRegex(m.IndexError, "symlink_rejected"):
                m.build_index(POLICY, metadata, evidence, repository, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.build(root)
            target = root / "target"
            target.mkdir()
            output = root / "output"
            output.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(m.IndexError, "directory_is_symlink"):
                m.write_outputs(output, result)

    def test_policy_and_source_preserve_offline_non_authority_boundary(self):
        policy = json.loads(POLICY.read_text())
        self.assertEqual(
            list(m.EXPECTED_CONTROLS),
            [item["control_id"] for item in policy["controls"]],
        )
        source = (
            ROOT / "scripts" / "build_external_control_evidence_index.py"
        ).read_text()
        for forbidden in (
            "subprocess",
            "urllib",
            "requests",
            "GITHUB_TOKEN",
            "DATABRICKS_TOKEN",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            "external_mutation_authorized",
            "branch_head_sha",
            "expected_verifier_sha256",
            "maximum_capture_spread_hours",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
