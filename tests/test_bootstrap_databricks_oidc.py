from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_databricks_oidc",
    ROOT / "scripts/bootstrap_databricks_oidc.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class FakeCli:
    def __init__(
        self,
        *,
        principals: dict[str, dict],
        policies: dict[str, list[dict]],
    ) -> None:
        self.principals = copy.deepcopy(principals)
        self.policies = copy.deepcopy(policies)
        self.commands: list[list[str]] = []

    def json(self, command):
        self.commands.append(list(command))
        if "service-principals" in command:
            numeric_id = command[
                command.index("service-principals") + 2
            ]
            return copy.deepcopy(self.principals[numeric_id])
        group = "service-principal-federation-policy"
        numeric_id = command[command.index(group) + 2]
        if "list" in command:
            return {
                "policies": copy.deepcopy(
                    self.policies.get(numeric_id, [])
                )
            }
        if "create" in command:
            payload = json.loads(
                command[command.index("--json") + 1]
            )
            self.policies.setdefault(numeric_id, []).append(
                {
                    **payload,
                    "service_principal_id": int(numeric_id),
                    "policy_id": (
                        f"created-{len(self.policies[numeric_id])}"
                    ),
                }
            )
            return {"policy_id": "created"}
        raise AssertionError(f"unexpected command: {command}")


class TestDatabricksBootstrap(unittest.TestCase):
    def make_config(self, root: Path):
        payload = {
            "repository": "alex/repo",
            "account_host": (
                "https://accounts.cloud.databricks.com"
            ),
            "account_id": "account-1",
            "audience": "https://github.com/alex",
            "principals": {
                name: {
                    "numeric_id": str(index + 100),
                    "application_id": f"app-{name}",
                }
                for index, name in enumerate(
                    m.REQUIRED_ENVIRONMENTS
                )
            },
        }
        path = root / "config.json"
        path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return m.load_config(path)

    @staticmethod
    def principal_payloads(config):
        return {
            principal.numeric_id: {
                "id": principal.numeric_id,
                "applicationId": principal.application_id,
                "active": True,
            }
            for principal in config.principals.values()
        }

    @staticmethod
    def exact_policies(config):
        return {
            principal.numeric_id: [
                {
                    **m.policy_payload(config, environment),
                    "service_principal_id": int(
                        principal.numeric_id
                    ),
                    "policy_id": f"policy-{environment}",
                }
            ]
            for environment, principal
            in config.principals.items()
        }

    def test_exact_subject(self):
        self.assertEqual(
            "repo:alex/repo:environment:dev-plan",
            m.subject("alex/repo", "dev-plan"),
        )

    def test_missing_policies_are_created_and_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            cli = FakeCli(
                principals=self.principal_payloads(config),
                policies={
                    principal.numeric_id: []
                    for principal in config.principals.values()
                },
            )
            result = m.ensure_policies(config, cli=cli)
        creates = [
            command
            for command in cli.commands
            if "create" in command
        ]
        self.assertEqual(4, len(creates))
        for command in creates:
            payload = json.loads(
                command[command.index("--json") + 1]
            )
            self.assertEqual(
                m.ISSUER,
                payload["oidc_policy"]["issuer"],
            )
        self.assertEqual(
            {"created"},
            {
                policy["outcome"]
                for policy in result["policies"]
            },
        )
        self.assertTrue(result["read_back_verified"])
        self.assertEqual("apply", result["mode"])

    def test_existing_exact_policies_are_verified_without_create(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            cli = FakeCli(
                principals=self.principal_payloads(config),
                policies=self.exact_policies(config),
            )
            result = m.ensure_policies(config, cli=cli)
        self.assertFalse(
            any("create" in command for command in cli.commands)
        )
        self.assertEqual(
            {"verified"},
            {
                policy["outcome"]
                for policy in result["policies"]
            },
        )

    def test_verify_is_read_only_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            cli = FakeCli(
                principals=self.principal_payloads(config),
                policies=self.exact_policies(config),
            )
            result = m.verify_policies(config, cli=cli)
        self.assertEqual("verify", result["mode"])
        self.assertEqual(0, result["write_operations"])
        self.assertTrue(
            all("create" not in command for command in cli.commands)
        )
        rendered = json.dumps(result)
        self.assertNotIn("app-dev-plan", rendered)
        self.assertNotIn("account-1", rendered)
        self.assertNotIn(
            "accounts.cloud.databricks.com",
            rendered,
        )

    def test_principal_mapping_mismatch_fails_before_create(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            principals = self.principal_payloads(config)
            principals["100"]["applicationId"] = "wrong-app"
            cli = FakeCli(
                principals=principals,
                policies={numeric_id: [] for numeric_id in principals},
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "does not match",
            ):
                m.ensure_policies(config, cli=cli)
        self.assertFalse(
            any("create" in command for command in cli.commands)
        )

    def test_subject_on_wrong_principal_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            policies = self.exact_policies(config)
            dev_policy = policies["100"].pop()
            policies["101"].append(dev_policy)
            cli = FakeCli(
                principals=self.principal_payloads(config),
                policies=policies,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "unexpected principal",
            ):
                m.verify_policies(config, cli=cli)

    def test_conflicting_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            policies = self.exact_policies(config)
            policies["100"][0]["oidc_policy"][
                "audiences"
            ] = ["https://wrong.example"]
            cli = FakeCli(
                principals=self.principal_payloads(config),
                policies=policies,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "conflicts",
            ):
                m.verify_policies(config, cli=cli)

    def test_verify_rejects_missing_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            policies = self.exact_policies(config)
            policies["100"] = []
            cli = FakeCli(
                principals=self.principal_payloads(config),
                policies=policies,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "is missing",
            ):
                m.verify_policies(config, cli=cli)

    def test_dry_run_redacts_identifiers_and_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            rendered = json.dumps(m.dry_run_summary(config))
        self.assertNotIn("app-dev-plan", rendered)
        self.assertNotIn("account-1", rendered)
        self.assertNotIn(
            "accounts.cloud.databricks.com",
            rendered,
        )

    def test_conflicting_application_ids_for_one_numeric_id_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "repository": "alex/repo",
                "account_host": (
                    "https://accounts.cloud.databricks.com"
                ),
                "account_id": "account-1",
                "audience": "https://github.com/alex",
                "principals": {
                    name: {
                        "numeric_id": "100",
                        "application_id": (
                            "app-one"
                            if name == "dev-plan"
                            else "app-two"
                        ),
                    }
                    for name in m.REQUIRED_ENVIRONMENTS
                },
            }
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(
                ValueError,
                "conflicting application IDs",
            ):
                m.load_config(path)


if __name__ == "__main__":
    unittest.main()
