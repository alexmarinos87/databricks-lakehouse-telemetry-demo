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
    "build_alert_delivery_evidence",
    ROOT / "scripts" / "build_alert_delivery_evidence.py",
)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def digest(value: bytes | str) -> str:
    payload = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class BuildAlertDeliveryEvidenceTest(unittest.TestCase):
    def metadata(self, artifact_sha256: str, *, notification_count: int = 1) -> dict:
        return {
            "schema_version": 1,
            "target": "dev",
            "repository": m.EXPECTED_REPOSITORY,
            "source_commit": "a" * 40,
            "captured_at_utc": (NOW - timedelta(minutes=1)).isoformat().replace(
                "+00:00", "Z"
            ),
            "workspace_fingerprint": digest("workspace"),
            "alert_event_id": "test-alert-20260825-001",
            "alert_id": "quality_error_check_failed",
            "severity": "critical",
            "owner": "data_engineering",
            "deployed_asset_fingerprint": digest("asset"),
            "destination_fingerprint": digest("destination"),
            "triggered_at_utc": (NOW - timedelta(minutes=20)).isoformat().replace(
                "+00:00", "Z"
            ),
            "delivered_at_utc": (NOW - timedelta(minutes=10)).isoformat().replace(
                "+00:00", "Z"
            ),
            "acknowledged_at_utc": (NOW - timedelta(minutes=5)).isoformat().replace(
                "+00:00", "Z"
            ),
            "resolved_at_utc": (NOW - timedelta(minutes=2)).isoformat().replace(
                "+00:00", "Z"
            ),
            "delivery_attempts": 1,
            "notification_count": notification_count,
            "delivery_status": "delivered",
            "acknowledging_owner": "data_engineering",
            "runbook": "docs/runbooks/operational_health.md#quality-errors",
            "test_alert": True,
            "protected_artifact": {
                "path": "delivery/evidence.json",
                "expected_sha256": artifact_sha256,
            },
        }

    def setup_inputs(
        self,
        root: Path,
        *,
        artifact: bytes = b"protected provider evidence",
        notification_count: int = 1,
    ) -> tuple[Path, Path]:
        artifact_root = root / "protected"
        artifact_path = artifact_root / "delivery" / "evidence.json"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_bytes(artifact)
        metadata_path = root / "metadata.json"
        metadata_path.write_text(
            json.dumps(self.metadata(digest(artifact), notification_count=notification_count)),
            encoding="utf-8",
        )
        return metadata_path, artifact_root

    def test_complete_package_hashes_protected_evidence_and_omits_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            result = m.build_package(
                metadata,
                artifact_root,
                root / "output",
                repository_root=ROOT,
                now=NOW,
            )
            manifest = json.loads((root / "output" / m.OUTPUT_MANIFEST).read_text())
            summary = (root / "output" / m.OUTPUT_SUMMARY).read_text()
            verification = json.loads(
                (root / "output" / m._verifier.OUTPUT_JSON).read_text()
            )

        self.assertEqual("verified", result["verification"]["status"])
        self.assertEqual(
            digest(b"protected provider evidence"), manifest["evidence_sha256"]
        )
        self.assertNotIn("protected_artifact", manifest)
        self.assertNotIn("delivery/evidence.json", json.dumps(manifest))
        self.assertNotIn("delivery/evidence.json", summary)
        self.assertEqual("verified", verification["status"])
        self.assertEqual(27, result["artifact_byte_count"])

    def test_digest_mismatch_fails_before_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            document = json.loads(metadata.read_text())
            document["protected_artifact"]["expected_sha256"] = digest("other")
            metadata.write_text(json.dumps(document))
            with self.assertRaisesRegex(m.PackageError, "digest_mismatch"):
                m.build_package(metadata, artifact_root, root / "output", now=NOW)
            self.assertFalse((root / "output" / m.OUTPUT_MANIFEST).exists())

    def test_traversal_absolute_backslash_and_symlink_paths_fail_closed(self):
        for value in (
            "../evidence.json",
            "/tmp/evidence.json",
            "delivery\\evidence.json",
        ):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    metadata, artifact_root = self.setup_inputs(root)
                    document = json.loads(metadata.read_text())
                    document["protected_artifact"]["path"] = value
                    metadata.write_text(json.dumps(document))
                    with self.assertRaisesRegex(m.PackageError, "artifact_path_invalid"):
                        m.build_package(metadata, artifact_root, root / "output", now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            real = artifact_root / "delivery" / "evidence.json"
            target = artifact_root / "delivery" / "target.json"
            real.rename(target)
            real.symlink_to(target)
            with self.assertRaisesRegex(m.PackageError, "symlink_rejected"):
                m.build_package(metadata, artifact_root, root / "output", now=NOW)

    def test_metadata_shape_rejects_raw_endpoint_and_supplied_digest(self):
        for key, value in (
            ("destination_url", "https://example.invalid"),
            ("evidence_sha256", digest("forged")),
        ):
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    metadata, artifact_root = self.setup_inputs(root)
                    document = json.loads(metadata.read_text())
                    document[key] = value
                    metadata.write_text(json.dumps(document))
                    with self.assertRaisesRegex(m.PackageError, "metadata_shape_invalid"):
                        m.build_package(metadata, artifact_root, root / "output", now=NOW)

    def test_blocked_verification_is_preserved_in_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root, notification_count=2)
            result = m.build_package(
                metadata,
                artifact_root,
                root / "output",
                repository_root=ROOT,
                now=NOW,
            )
        self.assertEqual("blocked", result["verification"]["status"])
        self.assertIn(
            "alert_notification_count_unexpected",
            {item["category"] for item in result["verification"]["findings"]},
        )

    def test_invalid_verifier_input_leaves_no_public_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            document = json.loads(metadata.read_text())
            document["source_commit"] = "not-a-commit"
            metadata.write_text(json.dumps(document))
            with self.assertRaisesRegex(m.PackageError, "source_commit_invalid"):
                m.build_package(
                    metadata,
                    artifact_root,
                    root / "output",
                    repository_root=ROOT,
                    now=NOW,
                )
            self.assertFalse((root / "output" / m.OUTPUT_MANIFEST).exists())
            self.assertFalse(
                (root / "output" / m._verifier.OUTPUT_JSON).exists()
            )
            self.assertEqual([], list((root / "output").glob("*.candidate")))

    def test_output_inside_protected_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            output = artifact_root / "public-package"
            with self.assertRaisesRegex(m.PackageError, "output_inside_protected_root"):
                m.build_package(metadata, artifact_root, output, now=NOW)
            self.assertFalse(output.exists())

    def test_symlink_root_metadata_and_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            root_target = root / "root-target"
            artifact_root.rename(root_target)
            artifact_root.symlink_to(root_target, target_is_directory=True)
            with self.assertRaisesRegex(m.PackageError, "artifact_root_invalid"):
                m.build_package(metadata, artifact_root, root / "output", now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            metadata_target = root / "metadata-target.json"
            metadata.rename(metadata_target)
            metadata.symlink_to(metadata_target)
            with self.assertRaisesRegex(m.PackageError, "metadata_not_regular"):
                m.build_package(metadata, artifact_root, root / "output", now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, artifact_root = self.setup_inputs(root)
            target = root / "target"
            target.mkdir()
            output = root / "output"
            output.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(m.PackageError, "output_directory_is_symlink"):
                m.build_package(metadata, artifact_root, output, now=NOW)

    def test_source_has_no_network_subprocess_or_provider_mutation_surface(self):
        source = (ROOT / "scripts" / "build_alert_delivery_evidence.py").read_text()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("databricks ", source.lower())
        self.assertNotIn("webhook", source.lower())
        self.assertIn("expected_sha256", source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("output_inside_protected_root", source)
        self.assertIn(".candidate", source)


if __name__ == "__main__":
    unittest.main()
