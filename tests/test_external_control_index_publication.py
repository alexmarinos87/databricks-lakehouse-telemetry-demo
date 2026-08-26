from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_external_control_evidence_index_publication",
    ROOT / "scripts" / "build_external_control_evidence_index.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class ExternalControlIndexPublicationTest(unittest.TestCase):
    @staticmethod
    def index() -> dict:
        return {
            "schema_version": 1,
            "status": "verified",
            "generated_at_utc": "2026-08-26T12:00:00Z",
            "target": "dev",
            "repository": "alexmarinos87/databricks-lakehouse-telemetry-demo",
            "source_commit": "a" * 40,
            "captured_at_utc": "2026-08-26T11:55:00Z",
            "policy_sha256": "sha256:" + "1" * 64,
            "metadata_sha256": "sha256:" + "2" * 64,
            "maximum_evidence_age_hours": 72,
            "maximum_capture_spread_hours": 4,
            "external_mutation_authorized": False,
            "controls": [],
            "findings": [],
        }

    @staticmethod
    def register_evidence_root(root: Path) -> Path:
        evidence_root = root / "evidence"
        evidence_root.mkdir()
        report = evidence_root / "report.json"
        report.write_text("{}", encoding="utf-8")
        m.io.protected_path(
            evidence_root,
            "report.json",
            prefix="external_control_test_report",
        )
        return evidence_root

    def test_complete_directory_is_published_once_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.register_evidence_root(root)
            output = root / "output"
            index = self.index()
            m.write_outputs(output, index)

            self.assertTrue((output / m.OUTPUT_JSON).is_file())
            self.assertTrue((output / m.OUTPUT_MARKDOWN).is_file())
            self.assertEqual(
                index,
                json.loads((output / m.OUTPUT_JSON).read_text()),
            )
            self.assertFalse((root / ".output.staging").exists())
            original = (output / m.OUTPUT_JSON).read_bytes()

            with self.assertRaisesRegex(m.IndexError, "directory_exists"):
                m.write_outputs(output, index)
            self.assertEqual(original, (output / m.OUTPUT_JSON).read_bytes())

    def test_output_inside_evidence_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_root = self.register_evidence_root(root)
            with self.assertRaisesRegex(m.IndexError, "inside_protected_root"):
                m.write_outputs(evidence_root / "index", self.index())
            self.assertFalse((evidence_root / "index").exists())

    def test_second_output_failure_leaves_no_public_or_staging_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.register_evidence_root(root)
            output = root / "output"
            original_write_text = Path.write_text

            def fail_markdown(path: Path, content: str, *args, **kwargs):
                if path.name == f".{m.OUTPUT_MARKDOWN}.tmp":
                    raise OSError("forced markdown write failure")
                return original_write_text(path, content, *args, **kwargs)

            with (
                mock.patch.object(Path, "write_text", new=fail_markdown),
                self.assertRaisesRegex(m.IndexError, "output_write_failed"),
            ):
                m.write_outputs(output, self.index())

            self.assertFalse(output.exists())
            self.assertFalse((root / ".output.staging").exists())

    def test_symlink_output_retains_specific_fail_closed_category(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.register_evidence_root(root)
            target = root / "target"
            target.mkdir()
            output = root / "output"
            output.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(m.IndexError, "directory_is_symlink"):
                m.write_outputs(output, self.index())


if __name__ == "__main__":
    unittest.main()
