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
    "build_development_runtime_evidence",
    ROOT / "scripts" / "build_development_runtime_evidence.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def digest(value: bytes | str) -> str:
    payload = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class BuildDevelopmentRuntimeEvidenceTest(unittest.TestCase):
    def inputs(self, root: Path, *, authorized: bool = True) -> tuple[Path, Path, dict]:
        artifact_root = root / "protected"
        artifact_root.mkdir()
        artifacts: list[dict] = []

        def add(artifact_id: str) -> str:
            payload = f"protected-{artifact_id}".encode()
            relative = f"evidence/{artifact_id}.json"
            path = artifact_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "path": relative,
                    "expected_sha256": digest(payload),
                }
            )
            return artifact_id

        execution = digest("execution")
        observed = utc(NOW - timedelta(minutes=20))
        document = {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": utc(NOW - timedelta(minutes=5)),
            "apply": {
                "authorized": authorized,
                "approved_at_utc": utc(NOW - timedelta(hours=1, minutes=10)),
                "approval_artifact_id": add("apply-approval"),
                "accepted_plan_artifact_id": add("accepted-plan"),
                "accepted_plan_review_artifact_id": add("accepted-plan-review"),
                "workflow_run_fingerprint": digest("workflow-run"),
            },
            "execution": {
                "execution_fingerprint": execution,
                "evidence_artifact_id": add("execution-record"),
                "started_at_utc": utc(NOW - timedelta(hours=1)),
                "completed_at_utc": observed,
                "production_contact": False,
                "deployment_principal_fingerprint": digest("deployment"),
                "runtime_principal_fingerprint": digest("runtime"),
            },
            "evidence_families": [
                {
                    "family": family,
                    "execution_fingerprint": execution,
                    "observed_at_utc": observed,
                    "evidence_artifact_id": add(f"family-{family}"),
                    "record_count": 1,
                }
                for family in m.verifier.REQUIRED_FAMILIES
            ],
            "assertions": [
                {
                    "assertion_id": assertion_id,
                    "family": family,
                    "execution_fingerprint": execution,
                    "status": "passed",
                    "observed_at_utc": observed,
                    "evidence_artifact_id": add(f"assertion-{assertion_id}"),
                }
                for assertion_id, family in m.verifier.ASSERTION_FAMILIES.items()
            ],
            "rollback": {
                "tested": True,
                "completed_at_utc": utc(NOW - timedelta(minutes=6)),
                "evidence_artifact_id": add("rollback"),
                "recovery_point_artifact_id": add("recovery-point"),
            },
            "protected_artifacts": artifacts,
        }
        metadata = root / "runtime-metadata.json"
        metadata.write_text(json.dumps(document), encoding="utf-8")
        return metadata, artifact_root, document

    def test_complete_package_hashes_all_artifacts_and_omits_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root, document = self.inputs(root)
            result = m.build_package(metadata, artifact_root, root / "output", now=NOW)
            manifest = json.loads((root / "output" / m.OUTPUT_MANIFEST).read_text())
            serialized = json.dumps(manifest)
        self.assertEqual("verified", result["verification"]["status"])
        self.assertEqual(len(document["protected_artifacts"]), result["artifact_count"])
        self.assertNotIn("protected_artifacts", manifest)
        self.assertNotIn("artifact_id", serialized)
        self.assertNotIn("evidence/", serialized)
        self.assertEqual(
            document["protected_artifacts"][0]["expected_sha256"],
            manifest["apply"]["approval_sha256"],
        )

    def test_digest_mismatch_unknown_duplicate_and_unused_registry_fail_closed(self):
        mutations = (
            ("artifact_digest_mismatch", lambda d: d["protected_artifacts"][0].update(expected_sha256=digest("wrong"))),
            ("artifact_reference_unknown", lambda d: d["apply"].update(approval_artifact_id="unknown-artifact")),
            ("artifact_id_duplicate", lambda d: d["protected_artifacts"].append(dict(d["protected_artifacts"][0]))),
            ("artifact_unused", lambda d: d["protected_artifacts"].append({"artifact_id": "unused-artifact", "path": "evidence/unused.json", "expected_sha256": digest("unused")})),
        )
        for category, mutate in mutations:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                metadata, artifact_root, document = self.inputs(root)
                if category == "artifact_unused":
                    path = artifact_root / "evidence" / "unused.json"
                    path.write_text("unused")
                mutate(document)
                metadata.write_text(json.dumps(document))
                with self.assertRaisesRegex(m.PackageError, category):
                    m.build_package(metadata, artifact_root, root / "output", now=NOW)

    def test_shared_reference_is_explicit_and_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root, document = self.inputs(root)
            shared = document["evidence_families"][0]["evidence_artifact_id"]
            displaced = document["assertions"][0]["evidence_artifact_id"]
            document["assertions"][0]["evidence_artifact_id"] = shared
            document["protected_artifacts"] = [
                item for item in document["protected_artifacts"]
                if item["artifact_id"] != displaced
            ]
            metadata.write_text(json.dumps(document))
            result = m.build_package(metadata, artifact_root, root / "output", now=NOW)
        self.assertEqual(
            result["manifest"]["evidence_families"][0]["evidence_sha256"],
            result["manifest"]["assertions"][0]["evidence_sha256"],
        )

    def test_exact_family_and_assertion_sets_and_blocked_status_are_preserved(self):
        for section, category in (
            ("evidence_families", "families_shape_invalid"),
            ("assertions", "assertions_shape_invalid"),
        ):
            with self.subTest(section=section), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                metadata, artifact_root, document = self.inputs(root)
                document[section].pop()
                metadata.write_text(json.dumps(document))
                with self.assertRaisesRegex(m.PackageError, category):
                    m.build_package(metadata, artifact_root, root / "output", now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root, _ = self.inputs(root, authorized=False)
            result = m.build_package(metadata, artifact_root, root / "output", now=NOW)
        self.assertEqual("blocked", result["verification"]["status"])

    def test_noncanonical_traversal_symlink_and_output_root_paths_fail_closed(self):
        for replacement, category in (
            ("evidence/./apply-approval.json", "path_not_canonical"),
            ("../outside.json", "path_invalid"),
        ):
            with self.subTest(path=replacement), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                metadata, artifact_root, document = self.inputs(root)
                document["protected_artifacts"][0]["path"] = replacement
                metadata.write_text(json.dumps(document))
                with self.assertRaisesRegex(m.PackageError, category):
                    m.build_package(metadata, artifact_root, root / "output", now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root, document = self.inputs(root)
            path = artifact_root / document["protected_artifacts"][0]["path"]
            target = path.with_name("target.json")
            path.rename(target)
            path.symlink_to(target)
            with self.assertRaisesRegex(m.PackageError, "symlink_rejected"):
                m.build_package(metadata, artifact_root, root / "output", now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root, _ = self.inputs(root)
            with self.assertRaisesRegex(m.PackageError, "inside_protected_root"):
                m.build_package(metadata, artifact_root, artifact_root / "public", now=NOW)

    def test_invalid_verifier_input_leaves_no_public_or_candidate_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root, document = self.inputs(root)
            document["source_commit"] = "not-a-commit"
            metadata.write_text(json.dumps(document))
            with self.assertRaisesRegex(m.PackageError, "source_commit_invalid"):
                m.build_package(metadata, artifact_root, root / "output", now=NOW)
            self.assertFalse((root / "output" / m.OUTPUT_MANIFEST).exists())
            self.assertEqual([], list((root / "output").glob("*.candidate")))

    def test_source_has_no_network_subprocess_credentials_or_provider_mutation(self):
        source = "\n".join(
            (ROOT / "scripts" / name).read_text()
            for name in (
                "build_development_runtime_evidence.py",
                "development_runtime_package_core.py",
                "protected_evidence_io.py",
            )
        )
        for forbidden in ("subprocess", "requests", "urllib", "DATABRICKS_TOKEN"):
            self.assertNotIn(forbidden, source)
        for required in (
            "O_NOFOLLOW", "protected_artifacts", ".candidate",
            "inside_protected_root", "path_not_canonical",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
