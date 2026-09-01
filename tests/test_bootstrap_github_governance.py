from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "m",
    ROOT / "scripts" / "bootstrap_github_governance.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class FakeClient:
    def __init__(self):
        self.calls = []

    def request(
        self,
        method,
        path,
        payload=None,
        acceptable_statuses=(200, 201, 204),
    ):
        self.calls.append((method, path, payload, acceptable_statuses))
        if method == "GET" and path == "/repos/alex/repo":
            return {
                "id": 123,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            }
        if method == "GET" and path.endswith("/branches/main"):
            return {"protected": True}
        if method == "GET" and path.endswith("/deployment-branch-policies"):
            return {"branch_policies": []}
        if method == "GET" and path.endswith("/variables"):
            return {"variables": [{"name": "DATABRICKS_HOST"}]}
        return {}


class TestGitHubBootstrap(unittest.TestCase):
    def make_config(self, root):
        payload = {
            "repository": "alex/repo",
            "environments": {
                name: {
                    "databricks_host": (
                        f"https://{name}.cloud.databricks.com"
                    ),
                    "databricks_client_id": f"client-{name}",
                }
                for name in m.REQUIRED_ENVIRONMENTS
            },
        }
        path = root / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return m.load_config(path)

    def test_protection_contract_requires_both_delivery_checks(self):
        payload = m.branch_protection_payload()
        checks = payload["required_status_checks"]

        self.assertTrue(checks["strict"])
        self.assertEqual(
            [
                "validate",
                "Round-trip synthetic review evidence",
            ],
            checks["contexts"],
        )
        self.assertEqual(
            tuple(checks["contexts"]),
            m.REQUIRED_STATUS_CONTEXTS,
        )
        self.assertEqual(
            len(checks["contexts"]),
            len(set(checks["contexts"])),
        )
        self.assertTrue(payload["enforce_admins"])
        self.assertTrue(payload["required_linear_history"])
        self.assertFalse(payload["allow_force_pushes"])
        self.assertFalse(payload["allow_deletions"])

    def test_apply_operations_and_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            result = m.apply_governance(
                self.make_config(Path(directory)),
                client=client,
            )

        self.assertTrue(result["protected"])
        self.assertEqual(
            list(m.REQUIRED_STATUS_CONTEXTS),
            result["required_status_contexts"],
        )
        paths = {
            (method, path)
            for method, path, _, _ in client.calls
        }
        self.assertIn(
            ("PUT", "/repos/alex/repo/branches/main/protection"),
            paths,
        )
        protection_call = next(
            call
            for call in client.calls
            if call[0] == "PUT"
            and call[1] == "/repos/alex/repo/branches/main/protection"
        )
        self.assertEqual(
            list(m.REQUIRED_STATUS_CONTEXTS),
            protection_call[2]["required_status_checks"]["contexts"],
        )
        for environment in m.REQUIRED_ENVIRONMENTS:
            self.assertIn(
                (
                    "PUT",
                    f"/repos/alex/repo/environments/{environment}",
                ),
                paths,
            )
        writes = [
            call
            for call in client.calls
            if "/variables" in call[1]
            and call[0] in {"POST", "PATCH"}
        ]
        self.assertEqual(8, len(writes))

    def test_dry_run_redacts_values_and_names_required_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = m.dry_run_summary(
                self.make_config(Path(directory)),
                required_approvals=0,
            )

        text = json.dumps(summary)
        self.assertNotIn("client-dev-plan", text)
        self.assertNotIn("dev-plan.cloud.databricks.com", text)
        self.assertEqual(
            list(m.REQUIRED_STATUS_CONTEXTS),
            summary["required_status_contexts"],
        )

    def test_requires_four_environments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "repository": "alex/repo",
                        "environments": {},
                    }
                )
            )
            with self.assertRaisesRegex(
                ValueError,
                "four required environments",
            ):
                m.load_config(path)


if __name__ == "__main__":
    unittest.main()
