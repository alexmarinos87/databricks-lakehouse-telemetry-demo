from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.ingestion_identity import (  # noqa: E402
    CHECKPOINT_POLICY,
    MODE_BACKFILL,
    MODE_INCREMENTAL,
    load_manifest,
    parse_object_name,
    plan_ingestion_uploads,
    validate_manifest,
    write_manifest,
)


class IngestionIdentityTest(unittest.TestCase):
    def write_csv(self, root: Path, relative: str, payload: bytes) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def test_repeated_incremental_planning_is_content_addressed_and_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_csv(root, "data/sample.csv", b"a,b\n1,2\n")

            first = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
            )
            second = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events/",
            )

            self.assertEqual(first, second)
            self.assertEqual(CHECKPOINT_POLICY, first["checkpoint_policy"])
            self.assertFalse(first["allow_overwrites"])
            entry = first["entries"][0]
            self.assertIn("machine-events__incremental__sha256_", entry["object_name"])
            self.assertNotIn("replay_", entry["object_name"])
            identity = parse_object_name(entry["object_name"])
            self.assertEqual(MODE_INCREMENTAL, identity.mode)
            self.assertIsNone(identity.replay_id)
            self.assertEqual(entry["sha256"], identity.sha256)

    def test_changed_content_uses_a_new_immutable_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_csv(root, "data/sample.csv", b"a,b\n1,2\n")
            first = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
            )
            source.write_bytes(b"a,b\n1,3\n")
            second = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
            )

            self.assertNotEqual(first["plan_id"], second["plan_id"])
            self.assertNotEqual(
                first["entries"][0]["destination_path"],
                second["entries"][0]["destination_path"],
            )

    def test_backfill_requires_and_embeds_a_safe_replay_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_csv(root, "data/sample.csv", b"a,b\n1,2\n")

            with self.assertRaisesRegex(ValueError, "requires a replay ID"):
                plan_ingestion_uploads(
                    [source],
                    repository_root=root,
                    destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                    mode=MODE_BACKFILL,
                )

            first = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                mode=MODE_BACKFILL,
                replay_id="repair-2026-08-22",
            )
            second = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                mode=MODE_BACKFILL,
                replay_id="repair-2026-08-23",
            )

            self.assertNotEqual(
                first["entries"][0]["destination_path"],
                second["entries"][0]["destination_path"],
            )
            identity = parse_object_name(first["entries"][0]["object_name"])
            self.assertEqual(MODE_BACKFILL, identity.mode)
            self.assertEqual("repair-2026-08-22", identity.replay_id)

    def test_incremental_mode_rejects_a_replay_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_csv(root, "data/sample.csv", b"a,b\n1,2\n")
            with self.assertRaisesRegex(ValueError, "must not define a replay ID"):
                plan_ingestion_uploads(
                    [source],
                    repository_root=root,
                    destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                    mode=MODE_INCREMENTAL,
                    replay_id="unexpected",
                )

    def test_manifest_detects_local_file_and_json_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_csv(root, "data/sample.csv", b"a,b\n1,2\n")
            manifest = plan_ingestion_uploads(
                [source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
            )
            manifest_path = root / "manifest.json"
            write_manifest(manifest, manifest_path)
            validated = load_manifest(manifest_path, repository_root=root)
            self.assertEqual(manifest["plan_id"], validated.plan_id)

            source.write_bytes(b"a,b\n9,9\n")
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                load_manifest(manifest_path, repository_root=root)

            source.write_bytes(b"a,b\n1,2\n")
            tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
            tampered["checkpoint_policy"] = "delete_checkpoint"
            with self.assertRaisesRegex(ValueError, "reuse the existing checkpoint"):
                validate_manifest(tampered, repository_root=root)

    def test_symlinks_and_files_outside_repository_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            external = self.write_csv(Path(outside), "external.csv", b"a,b\n1,2\n")
            link = root / "linked.csv"
            link.symlink_to(external)

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                plan_ingestion_uploads(
                    [link],
                    repository_root=root,
                    destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                )
            with self.assertRaisesRegex(ValueError, "inside the repository"):
                plan_ingestion_uploads(
                    [external],
                    repository_root=root,
                    destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                )

    def test_object_name_parser_rejects_backfill_without_replay_identity(self):
        digest = "a" * 64
        invalid = f"machine-events__backfill__sha256_{digest}.csv"
        with self.assertRaisesRegex(ValueError, "requires a replay ID"):
            parse_object_name(invalid)

    def test_incremental_identity_ignores_local_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_source = self.write_csv(root, "data/one.csv", b"a,b\n1,2\n")
            second_source = self.write_csv(root, "data/renamed.csv", b"a,b\n1,2\n")

            first = plan_ingestion_uploads(
                [first_source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
            )
            second = plan_ingestion_uploads(
                [second_source],
                repository_root=root,
                destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
            )

            self.assertEqual(
                first["entries"][0]["destination_path"],
                second["entries"][0]["destination_path"],
            )
            self.assertNotEqual(
                first["entries"][0]["source_name"],
                second["entries"][0]["source_name"],
            )

    def test_same_content_twice_in_one_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_source = self.write_csv(root, "data/one.csv", b"a,b\n1,2\n")
            second_source = self.write_csv(root, "data/two.csv", b"a,b\n1,2\n")

            with self.assertRaisesRegex(ValueError, "one destination"):
                plan_ingestion_uploads(
                    [first_source, second_source],
                    repository_root=root,
                    destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
                )


if __name__ == "__main__":
    unittest.main()
