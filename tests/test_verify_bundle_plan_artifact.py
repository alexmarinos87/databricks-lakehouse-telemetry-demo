from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "verify_bundle_plan_artifact",
    SCRIPTS / "verify_bundle_plan_artifact.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
import review_databricks_plan as plan_review


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class VerifyBundlePlanArtifactTest(unittest.TestCase):
    def make_artifact(
        self,
        root: Path,
        *,
        target: str = "dev",
        resources: dict[str, dict] | None = None,
    ) -> tuple[Path, m.ExpectedProvenance]:
        artifact = root / "artifact"
        artifact.mkdir()
        if resources is None:
            resources = {
                f"resources.jobs.{target}_loader": {
                    "action": "create",
                    "new_state": {"name": f"{target}-loader"},
                }
            }
        plan_document = {
            "plan_version": 2,
            "cli_version": "0.280.0",
            "lineage": "lineage-value",
            "serial": 7,
            "plan": resources,
            "not_selected": 0,
        }
        plan = (json.dumps(plan_document, sort_keys=True) + "\n").encode()
        validation = b"validation ok\n"
        (artifact / m.PLAN_FILE).write_bytes(plan)
        (artifact / m.VALIDATION_FILE).write_bytes(validation)
        (artifact / m.SUMMARY_FILE).write_text("summary\n", encoding="utf-8")

        policy = plan_review.load_policy(m.PLAN_REVIEW_POLICY, target)
        parsed = plan_review.parse_plan(artifact / m.PLAN_FILE, policy=policy)
        review = plan_review.review_plan(
            parsed,
            policy=policy,
            target=target,
            source_commit="a" * 40,
        )
        plan_review.write_evidence(artifact, review)

        evidence = {
            "schema_version": 2,
            "status": "succeeded",
            "mode": "plan",
            "target": target,
            "generated_at_utc": "2026-08-25T09:00:00Z",
            "completed_at_utc": "2026-08-25T09:00:01Z",
            "github": {
                "repository": "alex/repo",
                "ref": "refs/heads/main",
                "commit_sha": "a" * 40,
                "run_id": "123",
                "run_attempt": "1",
                "workflow": "Deploy Databricks Bundle",
            },
            "authentication": {
                "auth_type": "github-oidc",
                "host_fingerprint": fingerprint("host"),
                "configured_client_id_fingerprint": fingerprint("client"),
            },
            "identity": {
                "status": "succeeded",
                "active": True,
                "application_id_fingerprint": fingerprint("client"),
                "principal_id_fingerprint": fingerprint("principal"),
            },
            "validation": {
                "status": "succeeded",
                "format": "text",
                "output_file": m.VALIDATION_FILE,
                "output_bytes": len(validation),
                "output_sha256": hashlib.sha256(validation).hexdigest(),
            },
            "plan": {
                "status": "succeeded",
                "format": "json",
                "output_file": m.PLAN_FILE,
                "output_bytes": len(plan),
                "output_sha256": hashlib.sha256(plan).hexdigest(),
                "top_level_type": "object",
            },
            "review": {
                "status": review["status"],
                "schema_version": review["schema_version"],
                "policy_file": m.PLAN_REVIEW_POLICY_PATH,
                "json_file": m.PLAN_REVIEW_FILE,
                "markdown_file": m.PLAN_REVIEW_SUMMARY_FILE,
                "plan_sha256": review["plan_sha256"],
                "resource_count": review["resource_count"],
                "finding_count": len(review["findings"]),
            },
        }
        (artifact / m.EVIDENCE_FILE).write_text(
            json.dumps(evidence), encoding="utf-8"
        )
        expected = m.ExpectedProvenance(
            target,
            "alex/repo",
            "refs/heads/main",
            "a" * 40,
            "123",
            "1",
        )
        return artifact, expected

    def test_verified_artifact_recomputes_accepted_review(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            result = m.verify_artifact(artifact, expected)
        self.assertEqual("verified", result["status"])
        self.assertEqual("dev", result["target"])
        self.assertEqual("accepted", result["review_status"])

    def test_provenance_mismatches_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            cases = [
                (
                    m.ExpectedProvenance(
                        "prod",
                        expected.repository,
                        expected.ref,
                        expected.commit,
                        expected.run_id,
                        expected.run_attempt,
                    ),
                    "plan_evidence_target_mismatch",
                ),
                (
                    m.ExpectedProvenance(
                        expected.target,
                        "other/repo",
                        expected.ref,
                        expected.commit,
                        expected.run_id,
                        expected.run_attempt,
                    ),
                    "repository_provenance_mismatch",
                ),
                (
                    m.ExpectedProvenance(
                        expected.target,
                        expected.repository,
                        expected.ref,
                        "b" * 40,
                        expected.run_id,
                        expected.run_attempt,
                    ),
                    "commit_provenance_mismatch",
                ),
                (
                    m.ExpectedProvenance(
                        expected.target,
                        expected.repository,
                        expected.ref,
                        expected.commit,
                        "124",
                        expected.run_attempt,
                    ),
                    "run_id_provenance_mismatch",
                ),
            ]
            for candidate, category in cases:
                with self.subTest(category=category):
                    with self.assertRaisesRegex(m.ArtifactError, category):
                        m.verify_artifact(artifact, candidate)

    def test_plan_substitution_and_non_object_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            (artifact / m.PLAN_FILE).write_text(
                '{"changed":true}', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                m.ArtifactError,
                "bundle-plan.json_size_mismatch|bundle-plan.json_digest_mismatch",
            ):
                m.verify_artifact(artifact, expected)

        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            bad_plan = b"[]"
            (artifact / m.PLAN_FILE).write_bytes(bad_plan)
            evidence = json.loads((artifact / m.EVIDENCE_FILE).read_text())
            evidence["plan"]["output_bytes"] = len(bad_plan)
            evidence["plan"]["output_sha256"] = hashlib.sha256(
                bad_plan
            ).hexdigest()
            (artifact / m.EVIDENCE_FILE).write_text(json.dumps(evidence))
            with self.assertRaisesRegex(
                m.ArtifactError, "bundle_plan_unexpected_shape"
            ):
                m.verify_artifact(artifact, expected)

    def test_review_files_are_required_and_must_be_regular(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            (artifact / m.PLAN_REVIEW_FILE).unlink()
            with self.assertRaisesRegex(
                m.ArtifactError, "artifact_required_file_missing"
            ):
                m.verify_artifact(artifact, expected)

        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            (artifact / m.PLAN_REVIEW_SUMMARY_FILE).unlink()
            target = artifact / "real-review-summary"
            target.write_text("summary")
            (artifact / m.PLAN_REVIEW_SUMMARY_FILE).symlink_to(target)
            with self.assertRaisesRegex(
                m.ArtifactError, "artifact_contains_non_regular_entry"
            ):
                m.verify_artifact(artifact, expected)

    def test_blocked_review_cannot_be_relabelled_as_successful_plan(self):
        resources = {"resources.jobs.dev_obsolete": {"action": "delete"}}
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(
                Path(directory), resources=resources
            )
            with self.assertRaisesRegex(
                m.ArtifactError, "plan_review_not_accepted"
            ):
                m.verify_artifact(artifact, expected)

    def test_review_json_is_recomputed_from_exact_plan_and_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            stored = json.loads((artifact / m.PLAN_REVIEW_FILE).read_text())
            stored["resource_count"] += 1
            (artifact / m.PLAN_REVIEW_FILE).write_text(json.dumps(stored))
            (artifact / m.PLAN_REVIEW_SUMMARY_FILE).write_text(
                plan_review.render_summary(stored), encoding="utf-8"
            )
            evidence = json.loads((artifact / m.EVIDENCE_FILE).read_text())
            evidence["review"]["resource_count"] = stored["resource_count"]
            (artifact / m.EVIDENCE_FILE).write_text(json.dumps(evidence))
            with self.assertRaisesRegex(
                m.ArtifactError, "plan_review_recomputation_mismatch"
            ):
                m.verify_artifact(artifact, expected)

    def test_review_markdown_must_render_from_review_json(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            (artifact / m.PLAN_REVIEW_SUMMARY_FILE).write_text("tampered\n")
            with self.assertRaisesRegex(
                m.ArtifactError, "plan_review_markdown_mismatch"
            ):
                m.verify_artifact(artifact, expected)

    def test_current_policy_must_still_accept_the_exact_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, expected = self.make_artifact(root)
            original = m.PLAN_REVIEW_POLICY
            restrictive = root / "restrictive-policy.json"
            restrictive.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "required_plan_version": 2,
                        "targets": {
                            "dev": {
                                "allow_delete": False,
                                "allow_recreate": False,
                                "allow_gone_delete": True,
                                "forbidden_fragments": [
                                    "lakehouse_demo_prod",
                                    "/prod/",
                                    "prod-",
                                    "prod_",
                                ],
                                "max_create": 0,
                                "max_change": 100,
                                "max_delete": 0,
                                "max_recreate": 0,
                                "max_gone_delete": 25,
                                "max_permission_sensitive_resources": 25,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            try:
                m.PLAN_REVIEW_POLICY = restrictive
                with self.assertRaisesRegex(
                    m.ArtifactError, "plan_review_recomputation_not_accepted"
                ):
                    m.verify_artifact(artifact, expected)
            finally:
                m.PLAN_REVIEW_POLICY = original

    def test_unexpected_file_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            (artifact / "extra.txt").write_text("extra")
            with self.assertRaisesRegex(
                m.ArtifactError, "artifact_contains_unexpected_file"
            ):
                m.verify_artifact(artifact, expected)

        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            (artifact / m.SUMMARY_FILE).unlink()
            target = artifact / "real-summary"
            target.write_text("summary")
            (artifact / m.SUMMARY_FILE).symlink_to(target)
            with self.assertRaisesRegex(
                m.ArtifactError, "artifact_contains_non_regular_entry"
            ):
                m.verify_artifact(artifact, expected)

    def test_warning_metadata_and_bytes_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            warning = b"provider warning\n"
            (artifact / m.PLAN_WARNING_FILE).write_bytes(warning)
            evidence = json.loads((artifact / m.EVIDENCE_FILE).read_text())
            evidence["plan"].update(
                {
                    "warnings_file": m.PLAN_WARNING_FILE,
                    "warnings_bytes": len(warning),
                    "warnings_sha256": hashlib.sha256(warning).hexdigest(),
                }
            )
            (artifact / m.EVIDENCE_FILE).write_text(json.dumps(evidence))
            self.assertEqual(
                "verified", m.verify_artifact(artifact, expected)["status"]
            )
            (artifact / m.PLAN_WARNING_FILE).write_text("tampered")
            with self.assertRaisesRegex(
                m.ArtifactError,
                "bundle-plan-warnings.txt_size_mismatch|"
                "bundle-plan-warnings.txt_digest_mismatch",
            ):
                m.verify_artifact(artifact, expected)

    def test_evidence_shape_authentication_and_review_metadata_are_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            evidence = json.loads((artifact / m.EVIDENCE_FILE).read_text())
            evidence["unexpected"] = "value"
            (artifact / m.EVIDENCE_FILE).write_text(json.dumps(evidence))
            with self.assertRaisesRegex(m.ArtifactError, "evidence_shape_invalid"):
                m.verify_artifact(artifact, expected)

        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            evidence = json.loads((artifact / m.EVIDENCE_FILE).read_text())
            evidence["authentication"]["auth_type"] = "pat"
            (artifact / m.EVIDENCE_FILE).write_text(json.dumps(evidence))
            with self.assertRaisesRegex(
                m.ArtifactError, "authentication_type_mismatch"
            ):
                m.verify_artifact(artifact, expected)

        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            evidence = json.loads((artifact / m.EVIDENCE_FILE).read_text())
            evidence["review"]["status"] = "blocked"
            (artifact / m.EVIDENCE_FILE).write_text(json.dumps(evidence))
            with self.assertRaisesRegex(
                m.ArtifactError, "plan_review_metadata_not_accepted"
            ):
                m.verify_artifact(artifact, expected)

    def test_invalid_expected_ref_fails_before_artifact_use(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact, expected = self.make_artifact(Path(directory))
            bad = m.ExpectedProvenance(
                expected.target,
                expected.repository,
                "refs/heads/feature",
                expected.commit,
                expected.run_id,
                expected.run_attempt,
            )
            with self.assertRaisesRegex(m.ArtifactError, "expected_ref_invalid"):
                m.verify_artifact(artifact, bad)


if __name__ == "__main__":
    unittest.main()
