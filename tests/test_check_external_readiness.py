from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "check_external_readiness.py"
SPEC = importlib.util.spec_from_file_location("check_external_readiness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
check_external_readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_external_readiness)


ACCEPTED_SHA = "a" * 40
BASE_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example/request",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "short-lived-request-token",
    "GITHUB_REPOSITORY": "alexmarinos87/databricks-lakehouse-telemetry-demo",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": ACCEPTED_SHA,
    "GITHUB_RUN_ID": "12345",
    "GITHUB_RUN_ATTEMPT": "1",
    "DATABRICKS_AUTH_TYPE": "github-oidc",
    "DATABRICKS_HOST": "https://example.cloud.databricks.com",
    "DATABRICKS_CLIENT_ID": "expected-application-id",
    "GITHUB_TOKEN": "short-lived-github-token",
}


class FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._payload
        return self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def branch_payload(
    *,
    sha: str = ACCEPTED_SHA,
    protected: bool = True,
    contexts: list[str] | None = None,
) -> dict[str, object]:
    return {
        "name": "main",
        "commit": {"sha": sha},
        "protected": protected,
        "protection": {
            "required_status_checks": {
                "contexts": contexts if contexts is not None else ["validate"],
                "checks": [],
            }
        },
    }


def opener_for(payload: object):
    def opener(request, timeout):
        del timeout
        self_authorization = request.headers.get("Authorization", "")
        if "short-lived-github-token" not in self_authorization:
            raise AssertionError("workflow token was not supplied to the branch request")
        return FakeResponse(payload)

    return opener


class CheckExternalReadinessTest(unittest.TestCase):
    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    check_external_readiness.positive_seconds(value)
        self.assertEqual(2.5, check_external_readiness.positive_seconds("2.5"))

    def test_ready_preflight_writes_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "evidence"
            result = check_external_readiness.capture_readiness(
                repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
                branch="main",
                accepted_sha=ACCEPTED_SHA,
                output_directory=output_directory,
                environment=BASE_ENVIRONMENT,
                api_url="https://api.github.com",
                request_timeout_seconds=5,
                opener=opener_for(branch_payload()),
            )

            self.assertEqual(0, result)
            evidence_path = output_directory / "external-readiness.json"
            summary_path = output_directory / "external-readiness-summary.md"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            serialized = evidence_path.read_text(encoding="utf-8") + summary_path.read_text(
                encoding="utf-8"
            )

            self.assertEqual("ready", evidence["status"])
            self.assertEqual([], evidence["blockers"])
            self.assertTrue(evidence["github"]["accepted_sha_matches_head"])
            self.assertTrue(evidence["github"]["workflow_repository_matches"])
            self.assertTrue(
                evidence["github"]["accepted_sha_matches_workflow_sha"]
            )
            self.assertTrue(evidence["github"]["protected"])
            self.assertTrue(evidence["github"]["validate_required"])
            self.assertTrue(evidence["github"]["oidc_context_complete"])
            self.assertTrue(evidence["databricks"]["host_valid"])
            self.assertTrue(evidence["databricks"]["client_id_valid"])
            self.assertNotIn(BASE_ENVIRONMENT["DATABRICKS_HOST"], serialized)
            self.assertNotIn(BASE_ENVIRONMENT["DATABRICKS_CLIENT_ID"], serialized)
            self.assertNotIn(BASE_ENVIRONMENT["GITHUB_TOKEN"], serialized)
            self.assertNotIn(
                BASE_ENVIRONMENT["ACTIONS_ID_TOKEN_REQUEST_TOKEN"], serialized
            )

    def test_blocked_preflight_reports_all_independent_gates(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["DATABRICKS_HOST"] = ""
        environment["DATABRICKS_CLIENT_ID"] = ""
        environment["DATABRICKS_CLIENT_SECRET"] = "must-never-be-recorded"
        environment["ACTIONS_ID_TOKEN_REQUEST_URL"] = ""

        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "evidence"
            result = check_external_readiness.capture_readiness(
                repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
                branch="main",
                accepted_sha=ACCEPTED_SHA,
                output_directory=output_directory,
                environment=environment,
                api_url="https://api.github.com",
                request_timeout_seconds=5,
                opener=opener_for(
                    branch_payload(
                        sha="b" * 40,
                        protected=False,
                        contexts=[],
                    )
                ),
            )
            evidence_path = output_directory / "external-readiness.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            serialized = evidence_path.read_text(encoding="utf-8")

            self.assertEqual(1, result)
            self.assertEqual("blocked", evidence["status"])
            self.assertEqual(
                [
                    "accepted_main_is_stale",
                    "main_branch_is_unprotected",
                    "validate_check_is_not_required",
                    "databricks_host_is_missing",
                    "databricks_client_id_is_missing",
                    "static_client_secret_is_present",
                    "github_oidc_context_is_incomplete",
                ],
                evidence["blockers"],
            )
            self.assertEqual(
                ["ACTIONS_ID_TOKEN_REQUEST_URL"],
                evidence["github"]["missing_oidc_context"],
            )
            self.assertNotIn(environment["DATABRICKS_CLIENT_SECRET"], serialized)

    def test_workflow_provenance_mismatch_blocks_without_persisting_untrusted_repository(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["GITHUB_REPOSITORY"] = "untrusted/example"
        environment["GITHUB_SHA"] = "c" * 40

        evidence = check_external_readiness.build_evidence(
            repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
            branch="main",
            accepted_sha=ACCEPTED_SHA,
            environment=environment,
            branch_state={
                "head_sha": ACCEPTED_SHA,
                "protected": True,
                "required_status_contexts": ["validate"],
                "validate_required": True,
            },
        )
        serialized = json.dumps(evidence)

        self.assertEqual(
            [
                "github_repository_does_not_match",
                "accepted_main_does_not_match_workflow_sha",
            ],
            evidence["blockers"],
        )
        self.assertFalse(evidence["github"]["workflow_repository_matches"])
        self.assertFalse(
            evidence["github"]["accepted_sha_matches_workflow_sha"]
        )
        self.assertNotIn(environment["GITHUB_REPOSITORY"], serialized)

    def test_unapproved_api_url_is_rejected_before_token_use(self):
        def must_not_open(request, timeout):
            del request, timeout
            raise AssertionError("network request must not start")

        with self.assertRaises(check_external_readiness.ReadinessError) as error:
            check_external_readiness.fetch_branch_state(
                repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
                branch="main",
                api_url="https://attacker.example",
                token=BASE_ENVIRONMENT["GITHUB_TOKEN"],
                timeout_seconds=5,
                opener=must_not_open,
            )
        self.assertEqual("github_api_url_is_not_allowed", error.exception.category)

    def test_invalid_databricks_values_are_distinct_from_missing_values(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["DATABRICKS_HOST"] = "http://unsafe.example/path?token=secret"
        environment["DATABRICKS_CLIENT_ID"] = "invalid client id"

        evidence = check_external_readiness.build_evidence(
            repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
            branch="main",
            accepted_sha=ACCEPTED_SHA,
            environment=environment,
            branch_state={
                "head_sha": ACCEPTED_SHA,
                "protected": True,
                "required_status_contexts": ["validate"],
                "validate_required": True,
            },
        )
        serialized = json.dumps(evidence)

        self.assertEqual(
            ["databricks_host_is_invalid", "databricks_client_id_is_invalid"],
            evidence["blockers"],
        )
        self.assertNotIn(environment["DATABRICKS_HOST"], serialized)
        self.assertNotIn(environment["DATABRICKS_CLIENT_ID"], serialized)

    def test_branch_request_failure_is_sanitized_and_does_not_hide_other_gates(self):
        environment = dict(BASE_ENVIRONMENT)
        environment["DATABRICKS_HOST"] = ""

        def failing_opener(request, timeout):
            del request, timeout
            raise urllib.error.URLError("provider response must not be persisted")

        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "evidence"
            result = check_external_readiness.capture_readiness(
                repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
                branch="main",
                accepted_sha=ACCEPTED_SHA,
                output_directory=output_directory,
                environment=environment,
                api_url="https://api.github.com",
                request_timeout_seconds=5,
                opener=failing_opener,
            )
            evidence_path = output_directory / "external-readiness.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            serialized = evidence_path.read_text(encoding="utf-8")

            self.assertEqual(1, result)
            self.assertEqual(
                ["github_branch_state_unavailable", "databricks_host_is_missing"],
                evidence["blockers"],
            )
            self.assertEqual(
                "branch_request_failed",
                evidence["github"]["failure"]["category"],
            )
            self.assertNotIn("provider response must not be persisted", serialized)

    def test_output_directory_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)

            with self.assertRaises(check_external_readiness.ReadinessError) as error:
                check_external_readiness.capture_readiness(
                    repository="alexmarinos87/databricks-lakehouse-telemetry-demo",
                    branch="main",
                    accepted_sha=ACCEPTED_SHA,
                    output_directory=link,
                    environment=BASE_ENVIRONMENT,
                    api_url="https://api.github.com",
                    request_timeout_seconds=5,
                    opener=opener_for(branch_payload()),
                )
            self.assertEqual("output_directory_is_symlink", error.exception.category)


if __name__ == "__main__":
    unittest.main()
