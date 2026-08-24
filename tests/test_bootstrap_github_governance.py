from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_github_governance",
    ROOT / "scripts/bootstrap_github_governance.py",
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class FakeClient:
    def __init__(
        self,
        *,
        status_contexts: list[str] | None = None,
        variable_mismatch: bool = False,
        forbidden_secret: bool = False,
        reviewer_drift: bool = False,
        extra_branch_policy: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, object, tuple[int, ...]]] = []
        self.status_contexts = (
            [m.REQUIRED_STATUS_CONTEXT]
            if status_contexts is None
            else status_contexts
        )
        self.variable_mismatch = variable_mismatch
        self.forbidden_secret = forbidden_secret
        self.reviewer_drift = reviewer_drift
        self.extra_branch_policy = extra_branch_policy

    @staticmethod
    def _environment_from_path(path: str) -> str:
        marker = "/environments/"
        remainder = path.split(marker, 1)[1]
        return remainder.split("/", 1)[0]

    def request(
        self,
        method,
        path,
        payload=None,
        acceptable_statuses=(200, 201, 204),
    ):
        self.calls.append((method, path, payload, acceptable_statuses))
        if method != "GET":
            return {}
        if path == "/repos/alex/repo":
            return {
                "id": 123,
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
                "allow_update_branch": True,
            }
        if path == "/repos/alex/repo/branches/main":
            return {"protected": True}
        if path == "/repos/alex/repo/branches/main/protection":
            return {
                "required_status_checks": {
                    "strict": True,
                    "contexts": self.status_contexts,
                    "checks": [],
                },
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {
                    "dismiss_stale_reviews": True,
                    "required_approving_review_count": 0,
                },
                "required_linear_history": {"enabled": True},
                "allow_force_pushes": {"enabled": False},
                "allow_deletions": {"enabled": False},
                "required_conversation_resolution": {"enabled": True},
            }
        if (
            path.startswith("/repos/alex/repo/environments/")
            and path.endswith("/deployment-branch-policies")
        ):
            policies = [{"name": "main", "type": "branch"}]
            if self.extra_branch_policy:
                policies.append(
                    {"name": "release/*", "type": "branch"}
                )
            return {"branch_policies": policies}
        if (
            path.startswith("/repos/alex/repo/environments/")
            and "/deployment-branch-policies" not in path
        ):
            environment = self._environment_from_path(path)
            protection_rules = [
                {"type": "branch_policy"}
            ]
            if environment == "prod":
                reviewer_id = (
                    998 if self.reviewer_drift else 999
                )
                protection_rules.append(
                    {
                        "type": "required_reviewers",
                        "prevent_self_review": True,
                        "reviewers": [
                            {
                                "type": "User",
                                "reviewer": {
                                    "id": reviewer_id,
                                },
                            }
                        ],
                    }
                )
            return {
                "name": environment,
                "protection_rules": protection_rules,
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            }
        if path.endswith("/variables"):
            environment = self._environment_from_path(path)
            host = f"https://{environment}.cloud.databricks.com"
            client_id = f"client-{environment}"
            if self.variable_mismatch and environment == "dev-plan":
                client_id = "wrong-client"
            return {
                "variables": [
                    {"name": "DATABRICKS_HOST", "value": host},
                    {
                        "name": "DATABRICKS_CLIENT_ID",
                        "value": client_id,
                    },
                ]
            }
        if path.endswith("/secrets"):
            secrets = []
            if self.forbidden_secret:
                secrets.append(
                    {"name": m.FORBIDDEN_STATIC_CREDENTIAL}
                )
            return {"secrets": secrets}
        return {}


class TestGitHubBootstrap(unittest.TestCase):
    def make_config(self, root: Path):
        payload = {
            "repository": "alex/repo",
            "environments": {
                name: {
                    "databricks_host": (
                        f"https://{name}.cloud.databricks.com"
                    ),
                    "databricks_client_id": f"client-{name}",
                    "reviewers": (
                        [{"type": "User", "id": 999}]
                        if name == "prod"
                        else []
                    ),
                    "prevent_self_review": name == "prod",
                }
                for name in m.REQUIRED_ENVIRONMENTS
            },
        }
        path = root / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return m.load_config(path)

    def test_protection_contract(self):
        payload = m.branch_protection_payload()
        self.assertTrue(
            payload["required_status_checks"]["strict"]
        )
        self.assertEqual(
            ["validate"],
            payload["required_status_checks"]["contexts"],
        )
        self.assertTrue(payload["enforce_admins"])
        self.assertTrue(payload["required_linear_history"])
        self.assertFalse(payload["allow_force_pushes"])
        self.assertFalse(payload["allow_deletions"])

    def test_verify_governance_is_read_only_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient()
            result = m.verify_governance(
                config,
                client=client,
                required_approvals=0,
            )
        self.assertEqual("verify", result["mode"])
        self.assertEqual(0, result["write_operations"])
        self.assertTrue(
            all(method == "GET" for method, *_ in client.calls)
        )
        rendered = json.dumps(result)
        self.assertNotIn("client-dev-plan", rendered)
        self.assertNotIn(
            "dev-plan.cloud.databricks.com",
            rendered,
        )

    def test_verify_rejects_missing_required_status_context(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient(status_contexts=["old-check"])
            with self.assertRaisesRegex(
                m.GitHubApiError,
                "does not require validate",
            ):
                m.verify_governance(config, client=client)

    def test_verify_rejects_environment_variable_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient(variable_mismatch=True)
            with self.assertRaisesRegex(
                m.GitHubApiError,
                "does not match config",
            ):
                m.verify_governance(config, client=client)

    def test_verify_rejects_static_client_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient(forbidden_secret=True)
            with self.assertRaisesRegex(
                m.GitHubApiError,
                "forbidden static secret",
            ):
                m.verify_governance(config, client=client)

    def test_verify_rejects_environment_reviewer_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient(reviewer_drift=True)
            with self.assertRaisesRegex(
                m.GitHubApiError,
                "reviewer policy does not match config",
            ):
                m.verify_governance(config, client=client)

    def test_verify_rejects_additional_deployment_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient(extra_branch_policy=True)
            with self.assertRaisesRegex(
                m.GitHubApiError,
                "only the main branch",
            ):
                m.verify_governance(config, client=client)

    def test_apply_operations_are_followed_by_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(Path(directory))
            client = FakeClient()
            result = m.apply_governance(
                config,
                client=client,
            )
        self.assertEqual("apply", result["mode"])
        self.assertTrue(result["read_back_verified"])
        paths = {
            (method, path)
            for method, path, _, _ in client.calls
        }
        self.assertIn(
            (
                "PUT",
                "/repos/alex/repo/branches/main/protection",
            ),
            paths,
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
        first_write = next(
            index
            for index, call in enumerate(client.calls)
            if call[0] != "GET"
        )
        self.assertTrue(
            any(
                call[0] == "GET"
                and call[1].endswith("/protection")
                for call in client.calls[first_write + 1 :]
            )
        )

    def test_dry_run_redacts_values(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = m.dry_run_summary(
                self.make_config(Path(directory)),
                required_approvals=0,
            )
        text = json.dumps(summary)
        self.assertNotIn("client-dev-plan", text)
        self.assertNotIn(
            "dev-plan.cloud.databricks.com",
            text,
        )
        self.assertTrue(
            summary["read_back_verification_planned"]
        )

    def test_prod_requires_reviewer_and_self_review_prevention(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "repository": "alex/repo",
                "environments": {
                    name: {
                        "databricks_host": (
                            f"https://{name}.cloud.databricks.com"
                        ),
                        "databricks_client_id": f"client-{name}",
                        "reviewers": [],
                        "prevent_self_review": False,
                    }
                    for name in m.REQUIRED_ENVIRONMENTS
                },
            }
            path = Path(directory) / "bad-prod.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(
                ValueError,
                "prod requires a reviewer",
            ):
                m.load_config(path)

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
