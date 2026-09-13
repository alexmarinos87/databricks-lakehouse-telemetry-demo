"""Real temporary-Git and filesystem tests for source-artifact provenance."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("portfolio_ci_artifact", ROOT / "scripts/prepare_portfolio_ci_artifact.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class PortfolioCIArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.source = Path(self.temporary.name) / "snapshot"
        self.output = Path(self.temporary.name) / "artifact"
        self.sources = ["README.md", "scripts/build_portfolio_snapshot.py", "src/lakehouse_demo/portfolio_snapshot.py"]
        for relative in (*self.sources, *subject.PRODUCER_FILES):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Synthetic committed fixture for {relative}\n", encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Artifact test")
        self.git("config", "user.email", "artifact-test@example.invalid")
        self.commit()
        sha = self.git("rev-parse", "HEAD")
        self.env = {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": subject.REPOSITORY,
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": sha, "CANDIDATE_HEAD_SHA": sha, "CANDIDATE_BASE_SHA": "",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "CI_VALIDATION_RESULT": "success",
        }
        self.source.mkdir()
        self.write_snapshot()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic validation commit")

    def write_snapshot(self):
        payload = {
            "schema_version": 1, "snapshot_kind": "portfolio_source_evidence",
            "evidence_boundary": "repository_source_only",
            "verification": copy.deepcopy(subject.SOURCE_ONLY_VERIFICATION),
            "sources": [{"path": p, "sha256": hashlib.sha256((self.root / p).read_bytes()).hexdigest(),
                         "size_bytes": (self.root / p).stat().st_size} for p in sorted(self.sources)],
        }
        self.save_payload(payload)
        (self.source / subject.SNAPSHOT_FILES[1]).write_text("# Synthetic test snapshot\n", encoding="utf-8")

    def save_payload(self, payload):
        payload = {k: v for k, v in payload.items() if k != "snapshot_sha256"}
        payload["snapshot_sha256"] = hashlib.sha256(subject.canonical(payload).encode()).hexdigest()
        (self.source / subject.SNAPSHOT_FILES[0]).write_text(subject.canonical(payload), encoding="utf-8")

    def prepare(self):
        return subject.prepare(self.root, self.source, self.output, self.env)

    def assert_failure(self, operation, category=None):
        with self.assertRaises((subject.ArtifactError, subject.RepositoryFileError)) as raised:
            operation()
        if category:
            self.assertEqual(category, str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_push_packages_unchanged_snapshot_and_committed_source_evidence(self):
        result = self.prepare()
        self.assertEqual(set(subject.ARTIFACT_FILES), {p.name for p in self.output.iterdir()})
        for name in subject.SNAPSHOT_FILES:
            self.assertEqual((self.source / name).read_bytes(), (self.output / name).read_bytes())
        self.assertEqual(self.env["GITHUB_SHA"], result["tested_checkout"])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), result["tested_tree"])
        self.assertTrue(result["selected_source_bytes_match_checkout"])
        self.assertFalse(result["deployment_authorized"])
        self.assertEqual("pending_human_review", result["acceptance"])
        self.assertEqual(result, json.loads((self.output / "ci-provenance.json").read_text()))
        self.assertNotIn(self.temporary.name, json.dumps(result))

    def test_pr_records_distinct_base_head_and_actual_merge(self):
        base = self.env["GITHUB_SHA"]
        self.git("checkout", "-qb", "feature")
        (self.root / "feature.txt").write_text("feature")
        self.commit()
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "Synthetic PR merge", "feature")
        merge = self.git("rev-parse", "HEAD")
        self.env.update(GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/12/merge",
                        GITHUB_SHA=merge, CANDIDATE_HEAD_SHA=head, CANDIDATE_BASE_SHA=base)
        result = self.prepare()
        self.assertEqual((base, head, merge), (result["candidate_base"], result["candidate_head"], result["tested_checkout"]))
        self.assertEqual(3, len({base, head, merge}))
        self.output.rename(self.output.with_name("first"))
        self.env["CANDIDATE_HEAD_SHA"] = base
        self.assert_failure(self.prepare, "pull_request_parents_mismatch")

    def test_wrong_checkout_fails_before_output(self):
        self.env.update(GITHUB_SHA="0" * 40, CANDIDATE_HEAD_SHA="0" * 40)
        self.assert_failure(self.prepare, "checkout_identity_mismatch")

    def test_context_is_strict_and_non_success_cannot_publish(self):
        for key, value in {
            "GITHUB_REPOSITORY": "other/repo", "GITHUB_ACTIONS": "false",
            "GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_REF": "refs/heads/feature",
            "GITHUB_SHA": "$(private)", "GITHUB_RUN_ID": "0", "GITHUB_RUN_ATTEMPT": "1\nprivate",
            "CI_VALIDATION_RESULT": "failure", "CANDIDATE_BASE_SHA": "a" * 40,
        }.items():
            with self.subTest(key=key), mock.patch.dict(self.env, {key: value}):
                self.assert_failure(self.prepare, "ci_context_invalid")

    def test_run_attempt_changes_provenance_not_snapshot(self):
        first = self.prepare()
        self.output.rename(self.output.with_name("first"))
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        second = self.prepare()
        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(first["files"], second["files"])
        self.assertEqual(2, second["run_attempt"])
        self.assertNotEqual(first, second)

    def test_same_context_is_deterministic(self):
        first = self.prepare()
        self.output.rename(self.output.with_name("first"))
        self.assertEqual(first, self.prepare())

    def test_dirty_source_and_regenerated_uncommitted_snapshot_fail(self):
        (self.root / "README.md").write_text("modified")
        self.assert_failure(self.prepare, "snapshot_source_changed")
        self.write_snapshot()
        self.assert_failure(self.prepare, "source_not_committed")

    def test_untracked_selected_source_fails(self):
        self.sources.append("untracked.txt")
        (self.root / "untracked.txt").write_text("untracked")
        self.write_snapshot()
        self.assert_failure(self.prepare, "source_not_committed")

    def test_modified_producer_workflow_fails(self):
        (self.root / subject.PRODUCER_FILES[0]).write_text("changed workflow")
        self.assert_failure(self.prepare, "source_not_committed")

    def test_unselected_private_marker_is_neither_read_nor_exported(self):
        (self.root / "private.env").write_text("DO_NOT_EXPORT")
        with mock.patch.dict(os.environ, {"PRIVATE_TOKEN": "DO_NOT_EXPORT", "GIT_DIR": "/nonexistent"}):
            result = self.prepare()
        self.assertNotIn("DO_NOT_EXPORT", json.dumps(result))

    def test_stale_snapshot_digest_and_runtime_claims_fail(self):
        path = self.source / subject.SNAPSHOT_FILES[0]
        original = path.read_bytes()
        payload = json.loads(original)
        payload["extra"] = "changed"
        path.write_text(json.dumps(payload))
        self.assert_failure(self.prepare, "snapshot_digest_mismatch")
        payload.pop("extra")
        payload["verification"]["databricks_runtime"] = "passed"
        self.save_payload(payload)
        self.assert_failure(self.prepare, "snapshot_boundary_invalid")

    def test_duplicate_json_keys_fail(self):
        (self.source / subject.SNAPSHOT_FILES[0]).write_text('{"schema_version":1,"schema_version":1}')
        self.assert_failure(self.prepare, "snapshot_duplicate_key")

    def test_unsafe_duplicate_and_oversized_source_inventories_fail(self):
        original = json.loads((self.source / subject.SNAPSHOT_FILES[0]).read_text())
        for paths in (["../private"], ["/private"], ["a//b"], ["a/./b"], ["a*"], ["a", "a"], ["a"] * 101):
            with self.subTest(paths=paths[:2]):
                payload = copy.deepcopy(original)
                payload["sources"] = [{"path": p, "sha256": "0" * 64, "size_bytes": 1} for p in paths]
                self.save_payload(payload)
                self.assert_failure(self.prepare, "snapshot_sources_invalid")

    def test_extra_package_file_rejected(self):
        (self.source / "unexpected.txt").write_text("private")
        self.assert_failure(self.prepare, "package_file_set_invalid")

    def test_symlink_package_file_rejected(self):
        path = self.source / subject.SNAPSHOT_FILES[1]
        path.unlink()
        path.symlink_to(self.root / "README.md")
        self.assert_failure(self.prepare)

    def test_symlink_selected_source_rejected(self):
        path = self.root / "README.md"
        path.unlink()
        path.symlink_to(self.root / self.sources[1])
        self.assert_failure(self.prepare)

    def test_oversized_package_rejected(self):
        (self.source / subject.SNAPSHOT_FILES[1]).write_bytes(b"x" * 1_000_001)
        self.assert_failure(self.prepare)

    def test_existing_output_is_preserved(self):
        self.prepare()
        before = subject.package_bytes(self.output, subject.ARTIFACT_FILES)
        with self.assertRaises(subject.RepositoryFileError):
            self.prepare()
        self.assertEqual(before, subject.package_bytes(self.output, subject.ARTIFACT_FILES))

    def test_download_requires_exact_files_and_producer_bytes(self):
        self.prepare()
        downloaded = self.output.with_name("downloaded")
        shutil.copytree(self.output, downloaded)
        subject.verify_download(self.output, downloaded)
        path = downloaded / "ci-provenance.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(subject.ArtifactError, "download_bytes_mismatch"):
            subject.verify_download(self.output, downloaded)
        shutil.copyfile(self.output / path.name, path)
        (downloaded / "extra").write_text("extra")
        with self.assertRaisesRegex(subject.ArtifactError, "package_file_set_invalid"):
            subject.verify_download(self.output, downloaded)

    def test_download_symlink_rejected_even_with_identical_bytes(self):
        self.prepare()
        downloaded = self.output.with_name("downloaded")
        shutil.copytree(self.output, downloaded)
        path = downloaded / "portfolio-snapshot.md"
        path.unlink()
        path.symlink_to(self.output / path.name)
        with self.assertRaises(subject.RepositoryFileError):
            subject.verify_download(self.output, downloaded)

    def test_git_timeout_is_sanitized(self):
        with mock.patch.object(subject.subprocess, "run", side_effect=subprocess.TimeoutExpired("private", 10)):
            self.assert_failure(self.prepare, "git_read_failed")

    def test_cli_success_and_sanitized_failure(self):
        args = ["prepare", "--repository-root", str(self.root), "--snapshot-dir", str(self.source),
                "--output-dir", str(self.output)]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, subject.main(args))
        self.assertEqual({"status": "prepared"}, json.loads(out.getvalue()))
        self.assertEqual("", err.getvalue())
        with mock.patch.dict(os.environ, {**self.env, "GITHUB_RUN_ID": "private"}), contextlib.redirect_stderr(err):
            self.assertEqual(1, subject.main(args))
        self.assertEqual({"status": "failed", "category": "ci_context_invalid"}, json.loads(err.getvalue()))

    def test_workflow_publication_requires_validation_and_preserves_least_privilege(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        publication = text.split("  portfolio-evidence:", 1)[1]
        self.assertIn("needs: validate", publication)
        self.assertIn("CI_VALIDATION_RESULT: ${{ needs.validate.result }}", publication)
        self.assertIn("ref: ${{ github.sha }}", publication)
        self.assertIn("CANDIDATE_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}", publication)
        self.assertIn("CANDIDATE_BASE_SHA: ${{ github.event.pull_request.base.sha || '' }}", publication)
        for value in ("timeout-minutes: 5", "cancel-in-progress: true", "persist-credentials: false",
                      "retention-days: 7", "overwrite: false", "if-no-files-found: error",
                      "include-hidden-files: false", "digest-mismatch: error", "verify-download"):
            self.assertIn(value, publication)
        for forbidden in ("always()", "continue-on-error", "pull_request_target", "secrets.", "id-token:",
                          "environment:", "workflow_dispatch:", "schedule:", "contents: write"):
            self.assertNotIn(forbidden, publication)
        self.assertLess(publication.index("verify-download"), publication.index("Summarize verified"))
        for name in subject.ARTIFACT_FILES:
            self.assertIn("${{ runner.temp }}/portfolio-artifact/" + name, publication)
        for line in publication.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
