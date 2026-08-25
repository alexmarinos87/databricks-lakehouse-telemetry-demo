from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_databricks_federation",
    ROOT / "scripts" / "verify_databricks_federation.py",
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeInventory:
    def __init__(self, config: module.VerificationConfig) -> None:
        self.principals: dict[str, dict[str, object]] = {}
        self.policies: dict[str, list[dict[str, object]]] = {}
        self.secrets: dict[str, list[dict[str, object]]] = {}
        for environment, expectation in config.environments.items():
            for role, principal in (
                ("deployment", expectation.deployment),
                ("runtime", expectation.runtime),
            ):
                self.principals.setdefault(
                    principal.numeric_id,
                    {
                        "id": principal.numeric_id,
                        "applicationId": principal.application_id,
                        "active": True,
                        "roles": [],
                    },
                )
                policies = self.policies.setdefault(principal.numeric_id, [])
                subject = f"repo:{config.repository}:environment:{environment}"
                if not any(
                    item.get("oidc_policy", {}).get("subject") == subject
                    for item in policies
                ):
                    policies.append(
                        {
                            "service_principal_id": int(principal.numeric_id),
                            "policy_id": f"{role}-{environment}",
                            "oidc_policy": {
                                "issuer": module.ISSUER,
                                "audiences": [config.audience],
                                "subject": subject,
                                "subject_claim": "sub",
                            },
                        }
                    )
                self.secrets.setdefault(principal.numeric_id, [])

    def get_service_principal(self, numeric_id: str):
        return self.principals[numeric_id]

    def list_federation_policies(self, numeric_id: str):
        return list(self.policies[numeric_id])

    def list_service_principal_secrets(self, numeric_id: str):
        return list(self.secrets[numeric_id])


class VerifyDatabricksFederationTest(unittest.TestCase):
    def write_configs(self, root: Path) -> tuple[Path, Path]:
        deployment = {
            "repository": "alex/repo",
            "account_host": "https://accounts.cloud.databricks.com",
            "account_id": "account-1",
            "audience": "https://github.com/alex",
            "principals": {
                "dev-plan": {
                    "numeric_id": "100",
                    "application_id": "deploy-dev-plan",
                },
                "prod-plan": {
                    "numeric_id": "101",
                    "application_id": "deploy-prod-plan",
                },
                "dev": {"numeric_id": "102", "application_id": "deploy-dev"},
                "prod": {"numeric_id": "103", "application_id": "deploy-prod"},
            },
        }
        runtime = {
            "repository": "alex/repo",
            "account_host": "https://accounts.cloud.databricks.com",
            "account_id": "account-1",
            "audience": "https://github.com/alex",
            "environments": {
                "dev-plan": {
                    "deployment_client_id": "deploy-dev-plan",
                    "runtime_client_id": "runtime-dev",
                    "runtime_numeric_id": "200",
                },
                "prod-plan": {
                    "deployment_client_id": "deploy-prod-plan",
                    "runtime_client_id": "runtime-prod",
                    "runtime_numeric_id": "201",
                },
                "dev": {
                    "deployment_client_id": "deploy-dev",
                    "runtime_client_id": "runtime-dev",
                    "runtime_numeric_id": "200",
                },
                "prod": {
                    "deployment_client_id": "deploy-prod",
                    "runtime_client_id": "runtime-prod",
                    "runtime_numeric_id": "201",
                },
            },
        }
        deployment_path = root / "deployment.json"
        runtime_path = root / "runtime.json"
        deployment_path.write_text(json.dumps(deployment), encoding="utf-8")
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        return deployment_path, runtime_path

    def load(self, root: Path) -> module.VerificationConfig:
        deployment, runtime = self.write_configs(root)
        return module.load_config(deployment, runtime)

    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    module.positive_seconds(value)
        self.assertEqual(2.5, module.positive_seconds("2.5"))

    def test_configs_must_agree_on_deployment_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployment, runtime = self.write_configs(root)
            payload = json.loads(runtime.read_text(encoding="utf-8"))
            payload["environments"]["dev"]["deployment_client_id"] = "other"
            runtime.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs between configs"):
                module.load_config(deployment, runtime)

    def test_deployment_and_runtime_identities_cannot_overlap_globally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployment, runtime = self.write_configs(root)
            payload = json.loads(runtime.read_text(encoding="utf-8"))
            payload["environments"]["dev"]["runtime_numeric_id"] = "101"
            runtime.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlap across environments"):
                module.load_config(deployment, runtime)

    def test_verified_state_writes_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load(root)
            inventory = FakeInventory(config)
            output = root / "evidence"
            result = module.capture_verification(
                config=config,
                inventory=inventory,
                output_directory=output,
            )
            evidence = json.loads(
                (output / "databricks-federation-verification.json").read_text(
                    encoding="utf-8"
                )
            )
            rendered = json.dumps(evidence)
        self.assertEqual(0, result)
        self.assertEqual("verified", evidence["status"])
        self.assertEqual(6, len(evidence["principals"]))
        self.assertEqual([], evidence["findings"])
        for sensitive in (
            "accounts.cloud.databricks.com",
            "account-1",
            "deploy-dev-plan",
            "runtime-dev",
            '"numeric_id": "100"',
            "deployment-dev-plan",
        ):
            self.assertNotIn(sensitive, rendered)

    def test_missing_mismatched_duplicate_and_unexpected_policies_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load(root)
            inventory = FakeInventory(config)
            inventory.policies["100"] = []
            inventory.policies["101"][0]["oidc_policy"]["audiences"] = ["wrong"]
            inventory.policies["102"].append(dict(inventory.policies["102"][0]))
            inventory.policies["103"].append(
                {
                    "oidc_policy": {
                        "issuer": module.ISSUER,
                        "audiences": [config.audience],
                        "subject": "repo:other/repo:environment:prod",
                    }
                }
            )
            evidence = module.verify_state(config, inventory=inventory)
        categories = {item["category"] for item in evidence["findings"]}
        self.assertEqual("blocked", evidence["status"])
        self.assertTrue(
            {
                "federation_policy_missing",
                "federation_policy_mismatch",
                "duplicate_federation_policy",
                "unexpected_federation_policy",
            }.issubset(categories)
        )

    def test_inactive_admin_and_secret_bearing_principals_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load(root)
            inventory = FakeInventory(config)
            inventory.principals["100"]["active"] = False
            inventory.principals["101"]["roles"] = [{"value": "account_admin"}]
            inventory.secrets["102"] = [{"id": "secret-sensitive-id"}]
            evidence = module.verify_state(config, inventory=inventory)
            rendered = json.dumps(evidence)
        categories = {item["category"] for item in evidence["findings"]}
        self.assertTrue(
            {
                "service_principal_is_inactive",
                "service_principal_is_account_admin",
                "service_principal_has_oauth_secrets",
            }.issubset(categories)
        )
        self.assertNotIn("secret-sensitive-id", rendered)

    def test_application_and_numeric_mapping_drift_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load(root)
            inventory = FakeInventory(config)
            inventory.principals["100"]["id"] = "999"
            inventory.principals["101"]["applicationId"] = "wrong-app"
            evidence = module.verify_state(config, inventory=inventory)
        categories = {item["category"] for item in evidence["findings"]}
        self.assertIn("service_principal_numeric_id_mismatch", categories)
        self.assertIn("service_principal_application_id_mismatch", categories)

    def test_cli_commands_are_read_only_bounded_and_paginated(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.load(Path(directory))
        responses = iter(
            [
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout='{"id":"100"}', stderr=""
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout='{"policies":[],"next_page_token":"page-2"}',
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout='{"policies":[]}', stderr=""
                ),
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout='{"secrets":[]}', stderr=""
                ),
            ]
        )
        commands: list[list[str]] = []

        def runner(command, **kwargs):
            commands.append(command)
            self.assertEqual(17, kwargs["timeout"])
            return next(responses)

        with mock.patch.dict(
            os.environ,
            {"DATABRICKS_TOKEN": "", "DATABRICKS_CLIENT_SECRET": ""},
            clear=False,
        ):
            cli = module.DatabricksCli(config, timeout_seconds=17, runner=runner)
            cli.get_service_principal("100")
            cli.list_federation_policies("100")
            cli.list_service_principal_secrets("100")
        flattened = "\n".join(" ".join(command) for command in commands)
        self.assertIn("service-principals get 100", flattened)
        self.assertIn("service-principal-federation-policy list 100", flattened)
        self.assertIn("service-principal-secrets list 100", flattened)
        self.assertIn("--page-token page-2", flattened)
        for forbidden in (" create ", " update ", " delete ", " patch "):
            self.assertNotIn(forbidden, f" {flattened} ")

    def test_cli_timeout_and_repeated_page_token_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.load(Path(directory))

        def timeout_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with mock.patch.dict(
            os.environ,
            {"DATABRICKS_TOKEN": "", "DATABRICKS_CLIENT_SECRET": ""},
            clear=False,
        ):
            cli = module.DatabricksCli(config, runner=timeout_runner)
            with self.assertRaisesRegex(
                module.VerificationError, "command_timed_out"
            ) as raised:
                cli.get_service_principal("100")
        self.assertNotIn("100", str(raised.exception))

        responses = iter(
            [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout='{"policies":[],"next_page_token":"same"}',
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout='{"policies":[],"next_page_token":"same"}',
                    stderr="",
                ),
            ]
        )
        with mock.patch.dict(
            os.environ,
            {"DATABRICKS_TOKEN": "", "DATABRICKS_CLIENT_SECRET": ""},
            clear=False,
        ):
            cli = module.DatabricksCli(
                config, runner=lambda *args, **kwargs: next(responses)
            )
            with self.assertRaisesRegex(
                module.VerificationError, "pagination_token_repeated"
            ):
                cli.list_federation_policies("100")

    def test_static_auth_environment_and_symlink_output_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load(root)
            with mock.patch.dict(os.environ, {"DATABRICKS_TOKEN": "static-token"}):
                with self.assertRaisesRegex(
                    ValueError, "static Databricks credential"
                ):
                    module.DatabricksCli(config)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                module.VerificationError, "output_directory_is_symlink"
            ):
                module.capture_verification(
                    config=config,
                    inventory=FakeInventory(config),
                    output_directory=link,
                )

    def test_provider_failure_is_persisted_without_raw_diagnostics(self):
        class FailingInventory(FakeInventory):
            def get_service_principal(self, numeric_id: str):
                raise module.VerificationError("databricks", "command_failed")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.load(root)
            output = root / "evidence"
            result = module.capture_verification(
                config=config,
                inventory=FailingInventory(config),
                output_directory=output,
            )
            rendered = (
                output / "databricks-federation-verification.json"
            ).read_text(encoding="utf-8")
        self.assertEqual(1, result)
        self.assertIn('"category": "command_failed"', rendered)
        self.assertNotIn("accounts.cloud.databricks.com", rendered)
        self.assertNotIn("deploy-dev-plan", rendered)


if __name__ == "__main__":
    unittest.main()
