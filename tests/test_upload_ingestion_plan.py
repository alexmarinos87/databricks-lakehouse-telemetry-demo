from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODULE_PATH = ROOT / "scripts" / "upload_ingestion_plan.py"
SPEC = importlib.util.spec_from_file_location("upload_ingestion_plan", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
uploader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uploader)

from lakehouse_demo.ingestion_identity import (  # noqa: E402
    plan_ingestion_uploads,
    write_manifest,
)


class UploadIngestionPlanTest(unittest.TestCase):
    def make_plan(self, root: Path, payload: bytes = b"a,b\n1,2\n"):
        source = root / "data" / "sample.csv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        manifest = plan_ingestion_uploads(
            [source],
            repository_root=root,
            destination_root="dbfs:/Volumes/main/demo/files/raw_machine_events",
        )
        manifest_path = root / "upload-plan.json"
        write_manifest(manifest, manifest_path)
        return source, manifest, manifest_path

    def result(self, returncode: int, stdout: bytes = b""):
        return uploader.CommandResult(returncode=returncode, stdout=stdout)

    def test_existing_exact_object_is_a_verified_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, manifest_path = self.make_plan(root)
            payload = (root / manifest["entries"][0]["source_file"]).read_bytes()

            with mock.patch.object(
                uploader,
                "_run_command",
                side_effect=[self.result(0), self.result(0, payload)],
            ) as run:
                summary = uploader.upload_manifest(
                    target="dev",
                    manifest_path=manifest_path,
                    repository_root=root,
                )

            self.assertEqual(0, summary["uploaded"])
            self.assertEqual(1, summary["skipped"])
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn("mkdirs", commands[0])
            self.assertIn("cat", commands[1])
            self.assertFalse(any("cp" in command for command in commands))

    def test_missing_object_is_uploaded_without_overwrite_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, manifest_path = self.make_plan(root)
            payload = (root / manifest["entries"][0]["source_file"]).read_bytes()

            with mock.patch.object(
                uploader,
                "_run_command",
                side_effect=[
                    self.result(0),
                    self.result(1),
                    self.result(0),
                    self.result(0, payload),
                ],
            ) as run:
                summary = uploader.upload_manifest(
                    target="dev",
                    manifest_path=manifest_path,
                    repository_root=root,
                    command_timeout_seconds=17,
                )

            self.assertEqual(1, summary["uploaded"])
            self.assertEqual(0, summary["skipped"])
            commands = [call.args[0] for call in run.call_args_list]
            cp_command = next(command for command in commands if "cp" in command)
            self.assertNotIn("--overwrite", cp_command)
            self.assertFalse(any("rm" in command for command in commands))
            for call in run.call_args_list:
                self.assertEqual(17, call.kwargs["timeout_seconds"])

    def test_existing_content_mismatch_fails_without_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest_path = self.make_plan(root)

            with mock.patch.object(
                uploader,
                "_run_command",
                side_effect=[self.result(0), self.result(0, b"different")],
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "different content"):
                    uploader.upload_manifest(
                        target="dev",
                        manifest_path=manifest_path,
                        repository_root=root,
                    )

            self.assertFalse(any("cp" in call.args[0] for call in run.call_args_list))

    def test_concurrent_create_race_is_accepted_only_after_exact_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, manifest_path = self.make_plan(root)
            payload = (root / manifest["entries"][0]["source_file"]).read_bytes()

            with mock.patch.object(
                uploader,
                "_run_command",
                side_effect=[
                    self.result(0),
                    self.result(1),
                    self.result(9),
                    self.result(0, payload),
                ],
            ):
                summary = uploader.upload_manifest(
                    target="prod",
                    manifest_path=manifest_path,
                    repository_root=root,
                )

            self.assertEqual(0, summary["uploaded"])
            self.assertEqual(1, summary["skipped"])

    def test_copy_failure_omits_provider_output_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, manifest_path = self.make_plan(root)

            with mock.patch.object(
                uploader,
                "_run_command",
                side_effect=[
                    self.result(0),
                    self.result(1),
                    self.result(7, b"sensitive-provider-output"),
                    self.result(1),
                ],
            ):
                with self.assertRaisesRegex(RuntimeError, "exit code 7") as raised:
                    uploader.upload_manifest(
                        target="dev",
                        manifest_path=manifest_path,
                        repository_root=root,
                    )

            message = str(raised.exception)
            self.assertNotIn("sensitive-provider-output", message)
            self.assertNotIn(str(source), message)

    @mock.patch.object(uploader.subprocess, "run")
    def test_command_timeout_is_bounded_and_sanitized(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            cmd=["databricks", "fs", "cat", "sensitive-path"],
            timeout=5,
            output=b"sensitive-output",
        )
        with self.assertRaisesRegex(TimeoutError, "exceeded 5 seconds") as raised:
            uploader._run_command(
                ["databricks", "fs", "cat", "sensitive-path"],
                timeout_seconds=5,
            )
        self.assertNotIn("sensitive-path", str(raised.exception))
        self.assertNotIn("sensitive-output", str(raised.exception))

    def test_local_source_change_fails_before_databricks_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, manifest_path = self.make_plan(root)
            source.write_bytes(b"a,b\n9,9\n")

            with mock.patch.object(uploader, "_run_command") as run:
                with self.assertRaisesRegex(ValueError, "no longer matches"):
                    uploader.upload_manifest(
                        target="dev",
                        manifest_path=manifest_path,
                        repository_root=root,
                    )
            run.assert_not_called()

    def test_manifest_with_overwrite_or_checkpoint_reset_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, manifest_path = self.make_plan(root)
            for field, value, pattern in (
                ("allow_overwrites", True, "prohibit overwrites"),
                ("checkpoint_policy", "delete", "reuse the existing checkpoint"),
            ):
                with self.subTest(field=field):
                    tampered = dict(manifest)
                    tampered[field] = value
                    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
                    with mock.patch.object(uploader, "_run_command") as run:
                        with self.assertRaisesRegex(ValueError, pattern):
                            uploader.upload_manifest(
                                target="dev",
                                manifest_path=manifest_path,
                                repository_root=root,
                            )
                    run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
