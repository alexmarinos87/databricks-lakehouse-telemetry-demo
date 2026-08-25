from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("capture_databricks_plan", SCRIPTS / "capture_databricks_plan.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
import plan_evidence.core as core

BASE_ENV = {
    "GITHUB_ACTIONS": "true",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example/request",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "short-lived-request-token",
    "GITHUB_REPOSITORY": "alexmarinos87/databricks-lakehouse-telemetry-demo",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": "a" * 40,
    "GITHUB_RUN_ID": "12345",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW": "Deploy Databricks Bundle",
    "DATABRICKS_AUTH_TYPE": "github-oidc",
    "DATABRICKS_HOST": "https://example.cloud.databricks.com",
    "DATABRICKS_CLIENT_ID": "expected-app",
    "PATH": "/usr/bin:/bin",
}

def done(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(["databricks"], code, stdout, stderr)

def identity(app: str = "expected-app") -> str:
    return json.dumps({"id": "principal-id", "applicationId": app, "active": True})

class CaptureDatabricksPlanTest(unittest.TestCase):
    def test_positive_seconds_and_variables_fail_closed(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.assertRaises(argparse.ArgumentTypeError):
                m.positive_seconds(value)
        self.assertEqual(("catalog=main",), m.normalize_bundle_variables(["catalog=main"]))
        for values in (["bad"], ["x="], ["x=1", "x=2"], ["x=a\nb"], [f"v{i}=x" for i in range(33)]):
            with self.assertRaises(m.EvidenceError):
                m.normalize_bundle_variables(values)

    @mock.patch.object(core.subprocess, "run")
    def test_plan_mode_retains_exact_structured_plan(self, run):
        plan_text = '{"actions":[{"action":"create","resource":"job"}]}\n'
        run.side_effect = [done(identity()), done("validation output\n"), done(plan_text, "warning\n")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = m.capture_evidence(target="dev", mode="plan", output_directory=output,
                bundle_variables=("catalog=main",), environment=BASE_ENV,
                identity_timeout_seconds=11, validate_timeout_seconds=12, plan_timeout_seconds=13)
            evidence = json.loads((output / "evidence.json").read_text())
            self.assertEqual(0, result)
            self.assertEqual(2, evidence["schema_version"])
            self.assertEqual("json", evidence["plan"]["format"])
            self.assertEqual(m.PLAN_OUTPUT_FILE, evidence["plan"]["output_file"])
            self.assertEqual(plan_text, (output / m.PLAN_OUTPUT_FILE).read_text())
            self.assertEqual("validation output\n", (output / m.VALIDATION_OUTPUT_FILE).read_text())
        self.assertEqual(3, run.call_count)
        plan_command = run.call_args_list[2].args[0]
        self.assertEqual(["databricks", "bundle", "plan", "--target", "dev", "--output", "json"], plan_command[:7])
        self.assertEqual(13, run.call_args_list[2].kwargs["timeout"])

    @mock.patch.object(core.subprocess, "run")
    def test_identity_mode_does_not_run_bundle_commands(self, run):
        run.return_value = done(identity())
        with tempfile.TemporaryDirectory() as directory:
            result = m.capture_evidence(target="prod", mode="identity", output_directory=Path(directory),
                bundle_variables=(), environment=BASE_ENV, identity_timeout_seconds=5,
                validate_timeout_seconds=5, plan_timeout_seconds=5)
            evidence = json.loads((Path(directory) / "evidence.json").read_text())
        self.assertEqual(0, result)
        self.assertEqual(1, run.call_count)
        self.assertNotIn("plan", evidence)

    @mock.patch.object(core.subprocess, "run")
    def test_invalid_or_non_object_plan_fails_without_plan_file(self, run):
        for output, category in (("not-json", "invalid_json_response"), ("[]", "unexpected_json_shape")):
            run.reset_mock()
            run.side_effect = [done(identity()), done("ok"), done(output)]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                result = m.capture_evidence(target="dev", mode="plan", output_directory=path,
                    bundle_variables=(), environment=BASE_ENV, identity_timeout_seconds=5,
                    validate_timeout_seconds=5, plan_timeout_seconds=5)
                evidence = json.loads((path / "evidence.json").read_text())
                self.assertEqual(1, result)
                self.assertEqual(category, evidence["failure"]["category"])
                self.assertFalse((path / m.PLAN_OUTPUT_FILE).exists())
                self.assertNotIn(output, (path / "evidence.json").read_text())

    @mock.patch.object(core.subprocess, "run")
    def test_static_secret_and_missing_oidc_block_before_commands(self, run):
        for change, category in (({"DATABRICKS_CLIENT_SECRET": "secret"}, "static_client_secret_is_present"),
                                 ({"ACTIONS_ID_TOKEN_REQUEST_TOKEN": ""}, "github_oidc_context_is_incomplete")):
            env = dict(BASE_ENV); env.update(change)
            with tempfile.TemporaryDirectory() as directory:
                result = m.capture_evidence(target="dev", mode="identity", output_directory=Path(directory),
                    bundle_variables=(), environment=env, identity_timeout_seconds=5,
                    validate_timeout_seconds=5, plan_timeout_seconds=5)
                evidence = json.loads((Path(directory) / "evidence.json").read_text())
                self.assertEqual(1, result)
                self.assertEqual(category, evidence["failure"]["category"])
        run.assert_not_called()

    @mock.patch.object(core.subprocess, "run")
    def test_mismatched_identity_and_provider_failure_are_sanitized(self, run):
        for side_effect, category in (
            ([done(identity("wrong-app"))], "authenticated_identity_does_not_match_client_id"),
            ([done(identity()), done("sensitive", "diagnostic", 7)], "command_failed"),
        ):
            run.reset_mock(); run.side_effect = side_effect
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                result = m.capture_evidence(target="dev", mode="plan", output_directory=path,
                    bundle_variables=(), environment=BASE_ENV, identity_timeout_seconds=5,
                    validate_timeout_seconds=5, plan_timeout_seconds=5)
                text = (path / "evidence.json").read_text()
                evidence = json.loads(text)
                self.assertEqual(1, result)
                self.assertEqual(category, evidence["failure"]["category"])
                self.assertNotIn("wrong-app", text)
                self.assertNotIn("sensitive", text)
                self.assertNotIn("diagnostic", text)

    @mock.patch.object(core.subprocess, "run")
    def test_timeout_and_output_directory_symlink_fail_closed(self, run):
        run.side_effect = subprocess.TimeoutExpired(["databricks"], 5, output="sensitive")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence"
            result = m.capture_evidence(target="dev", mode="identity", output_directory=path,
                bundle_variables=(), environment=BASE_ENV, identity_timeout_seconds=5,
                validate_timeout_seconds=5, plan_timeout_seconds=5)
            self.assertEqual(1, result)
            self.assertNotIn("sensitive", (path / "evidence.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "target"; target.mkdir(); link = root / "link"; link.symlink_to(target)
            result = m.capture_evidence(target="dev", mode="identity", output_directory=link,
                bundle_variables=(), environment=BASE_ENV, identity_timeout_seconds=5,
                validate_timeout_seconds=5, plan_timeout_seconds=5)
            self.assertEqual(1, result)

if __name__ == "__main__":
    unittest.main()
