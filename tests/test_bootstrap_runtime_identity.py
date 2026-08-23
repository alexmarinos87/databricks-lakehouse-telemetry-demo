from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "bootstrap_runtime_identity.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_runtime_identity", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


class FakeGitHubClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, path, payload=None, expected=(200, 201, 204)):
        self.calls.append((method, path, payload, expected))
        if method == "GET" and path == "/repos/alex/repo":
            return {"id": 123}
        if method == "GET" and path.endswith("/variables"):
            return {"variables": []}
        return {}


class FakeDatabricksCli:
    def __init__(self, listed):
        self.listed = list(listed)
        self.commands = []

    def json(self, command):
        self.commands.append(command)
        if "list" in command:
            return self.listed.pop(0)
        return {"policy_id": "created"}


class RuntimeIdentityBootstrapTest(unittest.TestCase):
    def write_config(self, directory: Path, *, duplicate_identity: bool = False):
        environments = {}
        for index, name in enumerate(bootstrap.ENVIRONMENTS):
            deployment = f"deploy-{name}"
            runtime = deployment if duplicate_identity else f"runtime-{name}"
            environments[name] = {
                "deployment_client_id": deployment,
                "runtime_client_id": runtime,
                "runtime_numeric_id": str(1000 + index),
            }
        path = directory / "runtime.json"
        path.write_text(
            json.dumps(
                {
                    "repository": "alex/repo",
                    "account_host": "https://accounts.cloud.databricks.com",
                    "account_id": "account-1",
                    "audience": "https://github.com/alex",
                    "environments": environments,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_deployment_and_runtime_ids_must_be_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(Path(directory), duplicate_identity=True)
            with self.assertRaisesRegex(ValueError, "must be distinct"):
                bootstrap.load_config(path)

    def test_dry_run_redacts_all_identity_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config = bootstrap.load_config(self.write_config(Path(directory)))
            rendered = json.dumps(bootstrap.dry_run(config))

        self.assertNotIn("deploy-dev-plan", rendered)
        self.assertNotIn("runtime-dev-plan", rendered)
        self.assertNotIn("1000", rendered)
        self.assertIn("repo:alex/repo:environment:dev-plan", rendered)

    def test_github_apply_sets_runtime_client_id_for_each_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            config = bootstrap.load_config(self.write_config(Path(directory)))
            client = FakeGitHubClient()
            result = bootstrap.apply_github(config, client)

        writes = [call for call in client.calls if call[0] == "POST"]
        self.assertEqual(4, len(writes))
        self.assertEqual(4, len(result))
        for _, _, payload, _ in writes:
            self.assertEqual("DATABRICKS_RUNTIME_CLIENT_ID", payload["name"])
            self.assertTrue(payload["value"].startswith("runtime-"))

    def test_databricks_apply_creates_exact_subject_policies(self):
        with tempfile.TemporaryDirectory() as directory:
            config = bootstrap.load_config(self.write_config(Path(directory)))
            cli = FakeDatabricksCli(
                [{"policies": []} for _ in bootstrap.ENVIRONMENTS]
            )
            result = bootstrap.apply_databricks(config, cli)

        creates = [command for command in cli.commands if "create" in command]
        self.assertEqual(4, len(creates))
        self.assertEqual({"created"}, {item["outcome"] for item in result})
        for command in creates:
            payload = json.loads(command[command.index("--json") + 1])
            self.assertEqual(
                bootstrap.ISSUER, payload["oidc_policy"]["issuer"]
            )
            self.assertIn(":environment:", payload["oidc_policy"]["subject"])

    def test_conflicting_existing_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = bootstrap.load_config(self.write_config(Path(directory)))
            expected_subject = bootstrap.subject(config.repository, "dev-plan")
            cli = FakeDatabricksCli(
                [
                    {
                        "policies": [
                            {
                                "oidc_policy": {
                                    "issuer": bootstrap.ISSUER,
                                    "audiences": ["wrong"],
                                    "subject": expected_subject,
                                }
                            }
                        ]
                    }
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "conflicts"):
                bootstrap.apply_databricks(config, cli)


if __name__ == "__main__":
    unittest.main()
