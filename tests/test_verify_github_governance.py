from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_github_governance.py"
SPEC = importlib.util.spec_from_file_location("verify_github_governance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeClient:
    def __init__(self, config: module.VerificationConfig) -> None:
        self.config = config
        self.repository_overrides: dict[str, object] = {}
        self.protection_overrides: dict[str, object] = {}
        self.environment_overrides: dict[str, dict[str, object]] = {}
        self.policy_overrides: dict[str, list[dict[str, object]]] = {}
        self.variable_overrides: dict[str, list[dict[str, object]]] = {}
        self.secret_overrides: dict[str, list[dict[str, object]]] = {}
        self.fail_path: str | None = None
        self.branch_protected = True
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str):
        self.calls.append(("get", path))
        if self.fail_path == path:
            raise module.VerificationError("github", "request_failed")
        if path == f"/repos/{self.config.repository}":
            payload = {
                "id": 123,
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
                "allow_update_branch": True,
                "use_squash_pr_title_as_default": True,
                "squash_merge_commit_title": "PR_TITLE",
                "squash_merge_commit_message": "PR_BODY",
            }
            payload.update(self.repository_overrides)
            return payload
        if path == f"/repos/{self.config.repository}/branches/main":
            return {
                "protected": self.branch_protected,
                "commit": {"sha": "a" * 40},
            }
        if path == f"/repos/{self.config.repository}/branches/main/protection":
            payload = {
                "required_status_checks": {
                    "strict": True,
                    "contexts": list(module.REQUIRED_STATUS_CONTEXTS),
                    "checks": [],
                },
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {
                    "dismiss_stale_reviews": True,
                    "required_approving_review_count": 0,
                    "require_last_push_approval": False,
                },
                "required_linear_history": {"enabled": True},
                "allow_force_pushes": {"enabled": False},
                "allow_deletions": {"enabled": False},
                "required_conversation_resolution": {"enabled": True},
            }
            payload.update(self.protection_overrides)
            return payload
        prefix = f"/repos/{self.config.repository}/environments/"
        if path.startswith(prefix):
            environment = path.removeprefix(prefix)
            payload: dict[str, object] = {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            }
            payload.update(self.environment_overrides.get(environment, {}))
            return payload
        raise AssertionError(f"unexpected GET path: {path}")

    def list_all(self, path: str, key: str):
        self.calls.append(("list", path))
        if self.fail_path == path:
            raise module.VerificationError("github", "request_failed")
        for environment, expected in self.config.environments.items():
            if path.endswith(f"/environments/{environment}/deployment-branch-policies"):
                return self.policy_overrides.get(
                    environment, [{"name": "main", "type": "branch"}]
                )
            if path.endswith(f"/environments/{environment}/variables"):
                return self.variable_overrides.get(
                    environment,
                    [
                        {"name": "DATABRICKS_HOST", "value": expected.databricks_host},
                        {
                            "name": "DATABRICKS_CLIENT_ID",
                            "value": expected.deployment_client_id,
                        },
                        {
                            "name": "DATABRICKS_RUNTIME_CLIENT_ID",
                            "value": expected.runtime_client_id,
                        },
                    ],
                )
            if path.endswith(f"/environments/{environment}/secrets"):
                return self.secret_overrides.get(environment, [])
        raise AssertionError(f"unexpected LIST path: {path} ({key})")


class VerifyGitHubGovernanceTest(unittest.TestCase):
    def write_configs(self, root: Path, *, mismatch: bool = False):
        github_payload = {
            "repository": "alex/repo",
            "environments": {
                environment: {
                    "databricks_host": f"https://{environment}.cloud.databricks.com",
                    "databricks_client_id": f"deploy-{environment}",
                }
                for environment in module.REQUIRED_ENVIRONMENTS
            },
        }
        runtime_payload = {
            "repository": "alex/repo",
            "account_host": "https://accounts.cloud.databricks.com",
            "account_id": "account-1",
            "audience": "https://github.com/alex",
            "environments": {
                environment: {
                    "deployment_client_id": (
                        "different" if mismatch and environment == "dev-plan" else f"deploy-{environment}"
                    ),
                    "runtime_client_id": f"runtime-{environment}",
                    "runtime_numeric_id": str(100 + index),
                }
                for index, environment in enumerate(module.REQUIRED_ENVIRONMENTS)
            },
        }
        github_path = root / "github.json"
        runtime_path = root / "runtime.json"
        github_path.write_text(json.dumps(github_payload), encoding="utf-8")
        runtime_path.write_text(json.dumps(runtime_payload), encoding="utf-8")
        return github_path, runtime_path

    def load_config(self, root: Path):
        github_path, runtime_path = self.write_configs(root)
        return module.load_config(github_path, runtime_path)

    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    module.positive_seconds(value)
        self.assertEqual(2.5, module.positive_seconds("2.5"))

    def test_configs_must_agree_on_deployment_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            github_path, runtime_path = self.write_configs(
                Path(directory), mismatch=True
            )
            with self.assertRaisesRegex(ValueError, "differs between bootstrap configs"):
                module.load_config(github_path, runtime_path)

    def test_verified_state_writes_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load_config(root)
            client = FakeClient(config)
            output = root / "evidence"
            result = module.capture_verification(
                config=config,
                client=client,
                output_directory=output,
                required_approvals=0,
            )
            evidence_text = (output / "github-governance-verification.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(evidence_text)

        self.assertEqual(0, result)
        self.assertEqual("verified", evidence["status"])
        self.assertEqual([], evidence["findings"])
        self.assertEqual(
            list(module.REQUIRED_STATUS_CONTEXTS),
            evidence["branch_protection"]["expected_status_contexts"],
        )
        self.assertTrue(
            evidence["branch_protection"]["required_status_contexts_match"]
        )
        self.assertTrue(
            evidence["branch_protection"]["artifact_compatibility_required"]
        )
        self.assertTrue(all(item["verified"] for item in evidence["environments"]))
        for sensitive in (
            "dev-plan.cloud.databricks.com",
            "deploy-dev-plan",
            "runtime-dev-plan",
            "admin-token-value",
        ):
            self.assertNotIn(sensitive, evidence_text)

    def test_repository_and_branch_protection_drift_are_all_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.load_config(Path(directory))
            client = FakeClient(config)
            client.repository_overrides["allow_merge_commit"] = True
            client.protection_overrides.update(
                {
                    "required_status_checks": {"strict": False, "contexts": []},
                    "enforce_admins": {"enabled": False},
                    "allow_force_pushes": {"enabled": True},
                    "required_conversation_resolution": {"enabled": False},
                }
            )
            evidence = module.verify_state(config, client=client, required_approvals=0)

        categories = {finding["category"] for finding in evidence["findings"]}
        self.assertEqual("blocked", evidence["status"])
        self.assertTrue(
            {
                "repository_setting_drift",
                "required_checks_are_not_strict",
                "validate_check_is_not_required",
                "artifact_compatibility_check_is_not_required",
                "required_status_contexts_drift",
                "administrator_enforcement_is_disabled",
                "force_pushes_are_allowed",
                "conversation_resolution_is_not_required",
            }.issubset(categories)
        )

    def test_required_status_contexts_must_match_exactly(self):
        cases = {
            "missing_artifact_gate": ["validate"],
            "unexpected_context": [
                *module.REQUIRED_STATUS_CONTEXTS,
                "unreviewed-context",
            ],
        }
        for label, contexts in cases.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    config = self.load_config(Path(directory))
                    client = FakeClient(config)
                    client.protection_overrides["required_status_checks"] = {
                        "strict": True,
                        "contexts": contexts,
                        "checks": [],
                    }
                    evidence = module.verify_state(
                        config,
                        client=client,
                        required_approvals=0,
                    )

                categories = {
                    finding["category"] for finding in evidence["findings"]
                }
                self.assertEqual("blocked", evidence["status"])
                self.assertIn("required_status_contexts_drift", categories)
                self.assertFalse(
                    evidence["branch_protection"][
                        "required_status_contexts_match"
                    ]
                )
                if label == "missing_artifact_gate":
                    self.assertIn(
                        "artifact_compatibility_check_is_not_required",
                        categories,
                    )
                else:
                    self.assertTrue(
                        evidence["branch_protection"][
                            "artifact_compatibility_required"
                        ]
                    )

    def test_environment_policy_variable_and_secret_drift_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.load_config(Path(directory))
            client = FakeClient(config)
            client.environment_overrides["prod"] = {
                "deployment_branch_policy": {
                    "protected_branches": True,
                    "custom_branch_policies": False,
                }
            }
            client.policy_overrides["prod"] = [
                {"name": "main", "type": "branch"},
                {"name": "release/*", "type": "branch"},
            ]
            client.variable_overrides["prod"] = [
                {"name": "DATABRICKS_HOST", "value": "https://wrong.cloud.databricks.com"},
                {"name": "DATABRICKS_CLIENT_ID", "value": "deploy-prod"},
                {"name": "DATABRICKS_RUNTIME_CLIENT_ID", "value": "runtime-prod"},
                {"name": "DATABRICKS_CLIENT_SECRET", "value": "forbidden"},
            ]
            client.secret_overrides["prod"] = [
                {"name": "DATABRICKS_CLIENT_SECRET"}
            ]
            evidence = module.verify_state(config, client=client, required_approvals=0)

        categories = {finding["category"] for finding in evidence["findings"]}
        self.assertTrue(
            {
                "environment_branch_policy_drift",
                "environment_branch_scope_drift",
                "environment_variable_drift",
                "static_client_secret_variable_present",
                "static_client_secret_present",
            }.issubset(categories)
        )
        prod = next(item for item in evidence["environments"] if item["environment"] == "prod")
        self.assertFalse(prod["verified"])
        self.assertFalse(prod["static_client_secret_absent"])

    def test_unprotected_main_is_reported_without_requesting_protection_details(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.load_config(Path(directory))
            client = FakeClient(config)
            client.branch_protected = False
            evidence = module.verify_state(config, client=client, required_approvals=0)

        self.assertIn(
            "main_branch_is_unprotected",
            {finding["category"] for finding in evidence["findings"]},
        )
        self.assertNotIn(
            ("get", f"/repos/{config.repository}/branches/main/protection"),
            client.calls,
        )

    def test_required_approval_count_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.load_config(Path(directory))
            client = FakeClient(config)
            evidence = module.verify_state(config, client=client, required_approvals=1)
        self.assertIn(
            "required_approval_count_drift",
            {finding["category"] for finding in evidence["findings"]},
        )

    def test_api_failure_is_sanitized_in_persisted_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load_config(root)
            client = FakeClient(config)
            client.fail_path = f"/repos/{config.repository}/branches/main/protection"
            output = root / "evidence"
            result = module.capture_verification(
                config=config,
                client=client,
                output_directory=output,
                required_approvals=0,
            )
            rendered = (output / "github-governance-verification.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(rendered)

        self.assertEqual(1, result)
        self.assertEqual("failed", evidence["status"])
        self.assertEqual("request_failed", evidence["failure"]["category"])
        self.assertNotIn("admin-token-value", rendered)
        self.assertNotIn("provider response", rendered)

    def test_output_directory_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load_config(root)
            target = root / "target"
            target.mkdir()
            link = root / "evidence"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(module.VerificationError, "output_directory_is_symlink"):
                module.capture_verification(
                    config=config,
                    client=FakeClient(config),
                    output_directory=link,
                    required_approvals=0,
                )


if __name__ == "__main__":
    unittest.main()
