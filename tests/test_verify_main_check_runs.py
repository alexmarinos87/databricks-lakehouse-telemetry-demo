from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_main_check_runs.py"
SPEC = importlib.util.spec_from_file_location("verify_main_check_runs", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
COMMIT_SHA = "a" * 40
TOKEN = "short-lived-github-token"


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


def check_run(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    head_sha: str = COMMIT_SHA,
    app_slug: str = "github-actions",
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "app": {"slug": app_slug},
        "details_url": "https://provider.example/private-diagnostic?token=secret",
        "output": {"summary": "provider output must not be persisted"},
    }


def successful_payload() -> dict[str, object]:
    runs = [check_run(name) for name in module.REQUIRED_STATUS_CONTEXTS]
    return {"total_count": len(runs), "check_runs": runs}


def opener_for(payload: object, *, requests: list[object] | None = None):
    def opener(request, timeout):
        self_timeout = timeout
        if self_timeout <= 0:
            raise AssertionError("request timeout must be positive")
        authorization = request.headers.get("Authorization", "")
        if authorization != f"Bearer {TOKEN}":
            raise AssertionError("short-lived workflow token was not supplied")
        if requests is not None:
            requests.append(request)
        return FakeResponse(payload)

    return opener


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


class VerifyMainCheckRunsTest(unittest.TestCase):
    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    module.positive_seconds(value)
        self.assertEqual(2.5, module.positive_seconds("2.5"))

    def test_required_contexts_match_bootstrap_and_governance_verifier(self):
        bootstrap = load_module(
            "bootstrap_github_governance_for_check_test",
            ROOT / "scripts" / "bootstrap_github_governance.py",
        )
        governance = load_module(
            "verify_github_governance_for_check_test",
            ROOT / "scripts" / "verify_github_governance.py",
        )

        self.assertEqual(
            tuple(bootstrap.REQUIRED_STATUS_CONTEXTS),
            module.REQUIRED_STATUS_CONTEXTS,
        )
        self.assertEqual(
            tuple(governance.REQUIRED_STATUS_CONTEXTS),
            module.REQUIRED_STATUS_CONTEXTS,
        )

    def test_verified_check_runs_write_sanitized_evidence(self):
        requests: list[object] = []
        environment = {"GITHUB_TOKEN": TOKEN}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            result = module.capture_verification(
                repository=REPOSITORY,
                commit_sha=COMMIT_SHA,
                output_directory=output,
                environment=environment,
                api_url="https://api.github.com",
                timeout_seconds=5,
                opener=opener_for(successful_payload(), requests=requests),
            )
            evidence_path = output / "main-check-runs-verification.json"
            summary_path = output / "main-check-runs-verification.md"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            serialized = (
                evidence_path.read_text(encoding="utf-8")
                + summary_path.read_text(encoding="utf-8")
            )

        self.assertEqual(0, result)
        self.assertEqual("verified", evidence["status"])
        self.assertEqual([], evidence["blockers"])
        self.assertTrue(
            all(item["verified"] for item in evidence["required_checks"])
        )
        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual("GET", request.method)
        self.assertIn(
            f"/repos/alexmarinos87/databricks-lakehouse-telemetry-demo/"
            f"commits/{COMMIT_SHA}/check-runs?",
            request.full_url,
        )
        self.assertIn("filter=latest", request.full_url)
        self.assertIn("per_page=100", request.full_url)
        for forbidden in (
            TOKEN,
            "provider output must not be persisted",
            "private-diagnostic",
            "token=secret",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_missing_failing_ambiguous_and_wrong_app_checks_block(self):
        cases = {
            "missing": (
                [
                    check_run("Round-trip synthetic review evidence"),
                ],
                "required_check_run_is_missing:validate",
            ),
            "failing": (
                [
                    check_run("validate", conclusion="failure"),
                    check_run("Round-trip synthetic review evidence"),
                ],
                "required_check_run_is_not_successful:validate",
            ),
            "in_progress": (
                [
                    check_run("validate", status="in_progress", conclusion=None),
                    check_run("Round-trip synthetic review evidence"),
                ],
                "required_check_run_is_not_completed:validate",
            ),
            "ambiguous": (
                [
                    check_run("validate"),
                    check_run("validate"),
                    check_run("Round-trip synthetic review evidence"),
                ],
                "required_check_run_is_ambiguous:validate",
            ),
            "wrong_app": (
                [
                    check_run("validate", app_slug="untrusted-app"),
                    check_run("Round-trip synthetic review evidence"),
                ],
                "required_check_run_app_is_unexpected:validate",
            ),
            "wrong_commit": (
                [
                    check_run("validate", head_sha="b" * 40),
                    check_run("Round-trip synthetic review evidence"),
                ],
                "required_check_run_commit_mismatch:validate",
            ),
        }

        for label, (runs, expected_blocker) in cases.items():
            with self.subTest(label=label):
                evidence = module.build_evidence(
                    repository=REPOSITORY,
                    commit_sha=COMMIT_SHA,
                    check_runs=runs,
                )
                self.assertEqual("blocked", evidence["status"])
                self.assertIn(expected_blocker, evidence["blockers"])

    def test_inventory_truncation_writes_sanitized_failed_evidence(self):
        payload = {
            "total_count": 3,
            "check_runs": [
                check_run("validate"),
                check_run("Round-trip synthetic review evidence"),
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            result = module.capture_verification(
                repository=REPOSITORY,
                commit_sha=COMMIT_SHA,
                output_directory=output,
                environment={"GITHUB_TOKEN": TOKEN},
                api_url="https://api.github.com",
                timeout_seconds=5,
                opener=opener_for(payload),
            )
            evidence = json.loads(
                (output / "main-check-runs-verification.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(1, result)
        self.assertEqual("failed", evidence["status"])
        self.assertEqual(
            "check_run_inventory_is_truncated",
            evidence["failure"]["category"],
        )

    def test_provider_failure_is_sanitized(self):
        def failing_opener(request, timeout):
            del request, timeout
            raise urllib.error.URLError(
                "provider response and token must not be persisted"
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            result = module.capture_verification(
                repository=REPOSITORY,
                commit_sha=COMMIT_SHA,
                output_directory=output,
                environment={"GITHUB_TOKEN": TOKEN},
                api_url="https://api.github.com",
                timeout_seconds=5,
                opener=failing_opener,
            )
            rendered = (
                output / "main-check-runs-verification.json"
            ).read_text(encoding="utf-8")
            evidence = json.loads(rendered)

        self.assertEqual(1, result)
        self.assertEqual("failed", evidence["status"])
        self.assertEqual(
            "check_run_request_failed",
            evidence["failure"]["category"],
        )
        self.assertNotIn("provider response", rendered)
        self.assertNotIn(TOKEN, rendered)

    def test_unapproved_api_url_and_missing_token_fail_before_network_use(self):
        def must_not_open(request, timeout):
            del request, timeout
            raise AssertionError("network request must not start")

        cases = (
            (
                {"api_url": "https://attacker.example", "token": TOKEN},
                "github_api_url_is_not_allowed",
            ),
            (
                {"api_url": "https://api.github.com", "token": ""},
                "github_token_is_missing",
            ),
        )
        for kwargs, category in cases:
            with self.subTest(category=category):
                with self.assertRaises(module.CheckRunError) as error:
                    module.fetch_check_runs(
                        repository=REPOSITORY,
                        commit_sha=COMMIT_SHA,
                        timeout_seconds=5,
                        opener=must_not_open,
                        **kwargs,
                    )
                self.assertEqual(category, error.exception.category)

    def test_output_directory_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "evidence"
            link.symlink_to(target, target_is_directory=True)

            with self.assertRaises(module.CheckRunError) as error:
                module.capture_verification(
                    repository=REPOSITORY,
                    commit_sha=COMMIT_SHA,
                    output_directory=link,
                    environment={"GITHUB_TOKEN": TOKEN},
                    api_url="https://api.github.com",
                    timeout_seconds=5,
                    opener=opener_for(successful_payload()),
                )
        self.assertEqual("output_directory_is_symlink", error.exception.category)

    def test_source_is_read_only_and_has_no_token_argument(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('method="GET"', source)
        self.assertIn('os.environ.get("GITHUB_API_URL"', source)
        self.assertIn('environment.get("GITHUB_TOKEN", "")', source)
        self.assertNotIn('parser.add_argument("--token"', source)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertNotIn(f'method="{method}"', source)


if __name__ == "__main__":
    unittest.main()
