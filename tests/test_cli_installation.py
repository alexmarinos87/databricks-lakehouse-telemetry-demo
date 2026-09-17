"""Behavioral version checks and the credential-free installation workflow contract."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cli_installation", ROOT / "scripts/check_cli_installation.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class CLIInstallationTest(unittest.TestCase):
    def invoke(self, payload=None, returncode=0):
        if payload is None:
            payload = {"Version": "1.14.1", "IsSnapshot": False, "Prerelease": ""}
        with mock.patch.object(subject.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], returncode, json.dumps(payload).encode(), b"PRIVATE_DIAGNOSTIC",
        )):
            return subject.check_installation()

    def test_valid_release_has_only_installation_claim(self):
        self.assertEqual({"status": "verified", "version": "1.14.1",
                          "evidence_boundary": "local_cli_installation_only"}, self.invoke())

    def test_wrong_versions_and_nonrelease_builds_are_rejected(self):
        valid = {"Version": "1.14.1", "IsSnapshot": False, "Prerelease": ""}
        for change in ({"Version": "1.16.1"}, {"Version": "1.14.1-dev"}, {"Version": None},
                       {"IsSnapshot": True}, {"IsSnapshot": 0}, {"Prerelease": "rc1"}):
            with self.subTest(change=change), self.assertRaisesRegex(subject.CLIInstallationError, "cli_version_mismatch"):
                self.invoke({**valid, **change})

    def test_missing_fields_and_nonobjects_are_rejected(self):
        for payload in ({}, {"Version": "1.14.1"}, [], "version"):
            with self.subTest(payload=payload), self.assertRaises(subject.CLIInstallationError):
                self.invoke(payload)

    def test_malformed_duplicate_and_oversized_output_is_rejected(self):
        for raw in (b"not-json", b"\xff", b'{"Version":"1.14.1","Version":"1.16.1"}',
                    b"x" * (subject.MAX_VERSION_OUTPUT_BYTES + 1)):
            with self.subTest(length=len(raw)), mock.patch.object(subject.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 0, raw, b"private")):
                with self.assertRaisesRegex(subject.CLIInstallationError, "cli_version_invalid"):
                    subject.check_installation()

    def test_timeout_missing_binary_and_failed_command_are_sanitized(self):
        for error, category in ((subprocess.TimeoutExpired("private", 10), "cli_version_timeout"),
                                (FileNotFoundError("private"), "cli_unavailable")):
            with mock.patch.object(subject.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(subject.CLIInstallationError, category):
                    subject.check_installation()
        with self.assertRaisesRegex(subject.CLIInstallationError, "cli_version_command_failed"):
            self.invoke(returncode=1)

    def test_command_and_child_environment_cannot_request_workspace_auth(self):
        def check_call(args, **kwargs):
            self.assertEqual(["databricks", "version", "--output", "json"], args)
            self.assertEqual(subject.COMMAND_TIMEOUT_SECONDS, kwargs["timeout"])
            environment = kwargs["env"]
            self.assertEqual({"PATH", "HOME", "XDG_CONFIG_HOME", "DATABRICKS_CONFIG_FILE"}, set(environment))
            self.assertEqual(environment["HOME"], kwargs["cwd"])
            self.assertEqual([], list(Path(kwargs["cwd"]).iterdir()))
            self.assertNotIn("PRIVATE_MARKER", json.dumps(environment))
            return subprocess.CompletedProcess(args, 0, b'{"Version":"1.14.1","IsSnapshot":false,"Prerelease":""}', b"")
        with mock.patch.dict(os.environ, {"DATABRICKS_TOKEN": "PRIVATE_MARKER", "GITHUB_TOKEN": "PRIVATE_MARKER"}):
            with mock.patch.object(subject.subprocess, "run", side_effect=check_call):
                subject.check_installation()

    def test_real_subprocess_uses_isolated_home_and_no_inherited_tokens(self):
        # Execute an actual child process, but a synthetic CLI, not the installer.
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "databricks"
            binary.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                             'assert sys.argv[1:] == ["version", "--output", "json"]\n'
                             'assert "DATABRICKS_TOKEN" not in os.environ\n'
                             'assert "GITHUB_TOKEN" not in os.environ\n'
                             'assert not os.path.exists(os.environ["DATABRICKS_CONFIG_FILE"])\n'
                             'print(json.dumps({"Version":"1.14.1","IsSnapshot":False,"Prerelease":""}))\n')
            binary.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": temporary + os.pathsep + os.environ.get("PATH", os.defpath),
                                               "DATABRICKS_TOKEN": "PRIVATE_MARKER", "GITHUB_TOKEN": "PRIVATE_MARKER"}):
                self.assertEqual("verified", subject.check_installation()["status"])

    def test_cli_error_does_not_print_traceback_diagnostics_or_success(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(subject.subprocess, "run", side_effect=OSError("PRIVATE_DIAGNOSTIC")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(1, subject.main())
        self.assertEqual("", stdout.getvalue())
        self.assertEqual({"status": "failed", "category": "cli_unavailable"}, json.loads(stderr.getvalue()))

    def test_workflow_uses_exact_pin_version_and_acceptance_before_install(self):
        workflow = (ROOT / ".github/workflows/cli-installation-compatibility.yml").read_text()
        self.assertIn("databricks/setup-cli@cfd9223558b9082c2aabb0c8fa47f1c3db2b7cbd", workflow)
        self.assertIn('version: "' + subject.EXPECTED_VERSION + '"', workflow)
        self.assertLess(workflow.index("run: scripts/run_acceptance_checks.sh"), workflow.index("uses: databricks/setup-cli@"))
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.sha || github.event.before }}", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("timeout-minutes: 5", workflow)
        for forbidden in ("secrets.", "id-token:", "environment:", "pull_request_target:", "workflow_dispatch:",
                          "schedule:", "continue-on-error:", "always()", "bundle deploy", "bundle plan"):
            self.assertNotIn(forbidden, workflow)
        for line in workflow.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[0-9a-f]{40}$")

    def test_smoke_matches_deployment_contract_pin_and_cli_version(self):
        spec = importlib.util.spec_from_file_location("deployment_contract", ROOT / "tests/test_deployment_contract.py")
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        workflow = (ROOT / ".github/workflows/cli-installation-compatibility.yml").read_text()
        self.assertEqual(subject.EXPECTED_VERSION, contract.DATABRICKS_CLI_VERSION)
        self.assertIn(contract.SETUP_CLI_STEP, workflow)


if __name__ == "__main__":
    unittest.main()
