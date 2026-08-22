from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "capture_databricks_plan.py"
SPEC = importlib.util.spec_from_file_location("capture_databricks_plan", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
capture_databricks_plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_databricks_plan)


BASE_ENVIRONMENT = {
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
    "DATABRICKS_CLIENT_ID": "expected-application-id",
    "PATH": "/usr/bin:/bin",
}


def completed(command: list[str], stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)


class CaptureDatabricksPlanTest(unittest.TestCase):
    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    capture_databricks_plan.positive_seconds(value)
        self.assertEqual(2.5, capture_databricks_plan.positive_seconds("2.5"))

    def test_bundle_variables_are_bounded_and_fail_closed(self):
        self.assertEqual(
            ("catalog=main", "schema=demo"),
            capture_databricks_plan.normalize_bundle_variables(
                ["catalog=main", "schema=demo"]
            ),
        )
        invalid_values = (
            ["missing-separator"],
            ["bad-name!=value"],
            ["catalog="],
            ["catalog=one", "catalog=two"],
            ["catalog=line\nbreak"],
            [f"v{index}=x" for index in range(33)],
        )
        for values in invalid_values:
            with self.subTest(values=values[:2]):
                with self.assertRaises(capture_databricks_plan.EvidenceError):
                    capture_databricks_plan.normalize_bundle_variables(values)

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_plan_mode_captures_identity_validation_and_plan_evidence(self, run):
        identity_payload = json.dumps(
            {
                "id": "principal-object-id",
                "applicationId": BASE_ENVIRONMENT["DATABRICKS_CLIENT_ID"],
                "active": True,
            }
        )
        run.side_effect = [
            completed(["databricks"], stdout=identity_payload),
            completed(["databricks"], stdout="validation output\n"),
            completed(
                ["databricks"],
                stdout="plan output\n",
                stderr="reviewable warning\n",
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "evidence"
            result = capture_databricks_plan.capture_evidence(
                target="dev",
                mode="plan",
                output_directory=output_directory,
                bundle_variables=("catalog=main", "schema=demo"),
                environment=BASE_ENVIRONMENT,
                identity_timeout_seconds=11,
                validate_timeout_seconds=12,
                plan_timeout_seconds=13,
            )

            self.assertEqual(0, result)
            evidence = json.loads(
                (output_directory / "evidence.json").read_text(encoding="utf-8")
            )
            self.assertEqual("succeeded", evidence["status"])
            self.assertEqual("succeeded", evidence["identity"]["status"])
            self.assertEqual("succeeded", evidence["validation"]["status"])
            self.assertEqual("succeeded", evidence["plan"]["status"])
            self.assertEqual(
                "validation output\n",
                (output_directory / "bundle-validate.txt").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                "plan output\n",
                (output_directory / "bundle-plan.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "reviewable warning\n",
                (output_directory / "bundle-plan-warnings.txt").read_text(
                    encoding="utf-8"
                ),
            )

        self.assertEqual(3, run.call_count)
        self.assertEqual(
            ["databricks", "current-user", "me", "-o", "json"],
            run.call_args_list[0].args[0],
        )
        validate_command = run.call_args_list[1].args[0]
        plan_command = run.call_args_list[2].args[0]
        self.assertEqual(["databricks", "bundle", "validate"], validate_command[:3])
        self.assertEqual(["databricks", "bundle", "plan"], plan_command[:3])
        self.assertIn("catalog=main", validate_command)
        self.assertIn("schema=demo", plan_command)
        self.assertEqual(11, run.call_args_list[0].kwargs["timeout"])
        self.assertEqual(12, run.call_args_list[1].kwargs["timeout"])
        self.assertEqual(13, run.call_args_list[2].kwargs["timeout"])
        for call in run.call_args_list:
            self.assertNotIn("DATABRICKS_CLIENT_SECRET", call.kwargs["env"])

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_identity_mode_does_not_run_bundle_commands(self, run):
        run.return_value = completed(
            ["databricks"],
            stdout=json.dumps(
                {
                    "id": "principal-object-id",
                    "application_id": BASE_ENVIRONMENT["DATABRICKS_CLIENT_ID"],
                }
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = capture_databricks_plan.capture_evidence(
                target="prod",
                mode="identity",
                output_directory=Path(directory),
                bundle_variables=(),
                environment=BASE_ENVIRONMENT,
                identity_timeout_seconds=10,
                validate_timeout_seconds=10,
                plan_timeout_seconds=10,
            )
            evidence = json.loads(
                (Path(directory) / "evidence.json").read_text(encoding="utf-8")
            )

        self.assertEqual(0, result)
        self.assertEqual(1, run.call_count)
        self.assertEqual("succeeded", evidence["status"])
        self.assertNotIn("validation", evidence)
        self.assertNotIn("plan", evidence)

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_mismatched_identity_fails_without_persisting_raw_identifiers(self, run):
        run.return_value = completed(
            ["databricks"],
            stdout=json.dumps(
                {
                    "id": "raw-principal-object-id",
                    "applicationId": "unexpected-application-id",
                }
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            result = capture_databricks_plan.capture_evidence(
                target="dev",
                mode="plan",
                output_directory=output_directory,
                bundle_variables=(),
                environment=BASE_ENVIRONMENT,
                identity_timeout_seconds=10,
                validate_timeout_seconds=10,
                plan_timeout_seconds=10,
            )
            serialized = (output_directory / "evidence.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(serialized)

        self.assertEqual(1, result)
        self.assertEqual(
            "authenticated_identity_does_not_match_client_id",
            evidence["failure"]["category"],
        )
        self.assertNotIn("unexpected-application-id", serialized)
        self.assertNotIn("raw-principal-object-id", serialized)
        self.assertNotIn(BASE_ENVIRONMENT["DATABRICKS_CLIENT_ID"], serialized)
        self.assertEqual(1, run.call_count)

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_static_client_secret_is_rejected_before_any_command(self, run):
        environment = dict(BASE_ENVIRONMENT)
        environment["DATABRICKS_CLIENT_SECRET"] = "must-not-be-used"
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            result = capture_databricks_plan.capture_evidence(
                target="dev",
                mode="identity",
                output_directory=output_directory,
                bundle_variables=(),
                environment=environment,
                identity_timeout_seconds=10,
                validate_timeout_seconds=10,
                plan_timeout_seconds=10,
            )
            serialized = (output_directory / "evidence.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(serialized)

        self.assertEqual(1, result)
        self.assertEqual(
            "static_client_secret_is_present", evidence["failure"]["category"]
        )
        self.assertNotIn("must-not-be-used", serialized)
        run.assert_not_called()

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_failed_validate_records_only_bounded_output_metadata(self, run):
        identity_payload = json.dumps(
            {
                "id": "principal-object-id",
                "applicationId": BASE_ENVIRONMENT["DATABRICKS_CLIENT_ID"],
            }
        )
        run.side_effect = [
            completed(["databricks"], stdout=identity_payload),
            subprocess.CompletedProcess(
                ["databricks"],
                7,
                stdout="sensitive validation output",
                stderr="sensitive provider diagnostic",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            result = capture_databricks_plan.capture_evidence(
                target="dev",
                mode="plan",
                output_directory=output_directory,
                bundle_variables=(),
                environment=BASE_ENVIRONMENT,
                identity_timeout_seconds=10,
                validate_timeout_seconds=10,
                plan_timeout_seconds=10,
            )
            serialized = (output_directory / "evidence.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(serialized)

        self.assertEqual(1, result)
        self.assertEqual("validate", evidence["failure"]["stage"])
        self.assertEqual(7, evidence["failure"]["exit_code"])
        self.assertIn("sha256", evidence["failure"]["stdout"])
        self.assertIn("sha256", evidence["failure"]["stderr"])
        self.assertNotIn("sensitive validation output", serialized)
        self.assertNotIn("sensitive provider diagnostic", serialized)
        self.assertFalse((output_directory / "bundle-validate.txt").exists())
        self.assertEqual(2, run.call_count)

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_identity_timeout_is_bounded_and_sanitized(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            ["databricks", "current-user", "me"],
            timeout=5,
            output="partial sensitive output",
            stderr="partial sensitive error",
        )
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            result = capture_databricks_plan.capture_evidence(
                target="dev",
                mode="identity",
                output_directory=output_directory,
                bundle_variables=(),
                environment=BASE_ENVIRONMENT,
                identity_timeout_seconds=5,
                validate_timeout_seconds=10,
                plan_timeout_seconds=10,
            )
            serialized = (output_directory / "evidence.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(serialized)

        self.assertEqual(1, result)
        self.assertEqual("command_timed_out", evidence["failure"]["category"])
        self.assertNotIn("partial sensitive output", serialized)
        self.assertNotIn("partial sensitive error", serialized)
        self.assertEqual(1, run.call_count)

    @mock.patch.object(capture_databricks_plan.subprocess, "run")
    def test_missing_github_oidc_context_fails_before_any_command(self, run):
        environment = dict(BASE_ENVIRONMENT)
        del environment["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
        with tempfile.TemporaryDirectory() as directory:
            result = capture_databricks_plan.capture_evidence(
                target="prod",
                mode="identity",
                output_directory=Path(directory),
                bundle_variables=(),
                environment=environment,
                identity_timeout_seconds=10,
                validate_timeout_seconds=10,
                plan_timeout_seconds=10,
            )
            evidence = json.loads(
                (Path(directory) / "evidence.json").read_text(encoding="utf-8")
            )

        self.assertEqual(1, result)
        self.assertEqual(
            "github_oidc_context_is_incomplete", evidence["failure"]["category"]
        )
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
