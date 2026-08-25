from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "review_databricks_plan", ROOT / "scripts" / "review_databricks_plan.py"
)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


class ReviewDatabricksPlanTest(unittest.TestCase):
    source_commit = "a" * 40

    def write_policy(
        self,
        root: Path,
        *,
        allow_delete: bool = False,
        allow_recreate: bool = False,
        allow_gone_delete: bool = True,
        max_permission_sensitive_resources: int = 25,
    ) -> Path:
        payload = {
            "schema_version": 2,
            "required_plan_version": 2,
            "targets": {
                "dev": {
                    "allow_delete": allow_delete,
                    "allow_recreate": allow_recreate,
                    "allow_gone_delete": allow_gone_delete,
                    "forbidden_fragments": [
                        "lakehouse_demo_prod",
                        "/prod/",
                        "prod-",
                        "prod_",
                    ],
                    "max_create": 100,
                    "max_change": 100,
                    "max_delete": 100 if allow_delete else 0,
                    "max_recreate": 100 if allow_recreate else 0,
                    "max_gone_delete": 25,
                    "max_permission_sensitive_resources": (
                        max_permission_sensitive_resources
                    ),
                }
            },
        }
        path = root / "policy.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def plan(
        self,
        resources: dict[str, dict],
        **metadata,
    ) -> dict:
        return {
            "plan_version": 2,
            "cli_version": "0.280.0",
            "lineage": "lineage-value",
            "serial": 7,
            "plan": resources,
            **metadata,
        }

    def evaluate(
        self,
        root: Path,
        payload: dict,
        **policy_options,
    ) -> dict:
        policy = review.load_policy(
            self.write_policy(root, **policy_options),
            "dev",
        )
        plan_path = root / "bundle-plan.json"
        plan_path.write_text(json.dumps(payload), encoding="utf-8")
        parsed = review.parse_plan(plan_path, policy=policy)
        return review.review_plan(
            parsed,
            policy=policy,
            target="dev",
            source_commit=self.source_commit,
        )

    def test_clean_create_and_update_plan_is_accepted(self):
        payload = self.plan(
            {
                "resources.jobs.dev_loader": {
                    "action": "create",
                    "new_state": {"name": "dev-loader"},
                },
                "resources.pipelines.dev_quality": {
                    "action": "update",
                    "changes": {
                        "name": {
                            "action": "update",
                            "old": "old",
                            "new": "new",
                            "remote": "old",
                        }
                    },
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = self.evaluate(Path(directory), payload)
        self.assertEqual("accepted", evidence["status"])
        self.assertEqual(1, evidence["resource_actions"]["create"])
        self.assertEqual(1, evidence["resource_actions"]["change"])
        self.assertEqual([], evidence["findings"])

    def test_empty_plan_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = self.evaluate(Path(directory), self.plan({}))
        self.assertEqual("accepted", evidence["status"])
        self.assertEqual(0, evidence["resource_count"])

    def test_delete_recreate_and_gone_delete_are_classified_separately(self):
        payload = self.plan(
            {
                "resources.jobs.dev_delete": {"action": "delete"},
                "resources.jobs.dev_recreate": {
                    "action": "recreate",
                    "changes": {
                        "name": {
                            "action": "recreate",
                            "old": "old",
                            "new": "new",
                        }
                    },
                },
                "resources.jobs.dev_gone": {
                    "action": "delete",
                    "gone": True,
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = self.evaluate(Path(directory), payload)
        categories = {item["category"] for item in evidence["findings"]}
        self.assertIn("delete_is_not_allowed", categories)
        self.assertIn("recreate_is_not_allowed", categories)
        self.assertNotIn("gone_delete_is_not_allowed", categories)
        self.assertEqual(1, evidence["resource_actions"]["gone_delete"])
        self.assertEqual(1, evidence["resource_actions"]["destructive_delete"])

    def test_cross_target_value_is_blocked_without_persisting_value(self):
        forbidden = "/Shared/lakehouse_demo_prod/reporting"
        payload = self.plan(
            {
                "resources.jobs.dev_loader": {
                    "action": "create",
                    "new_state": {"path": forbidden},
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = self.evaluate(Path(directory), payload)
        rendered = json.dumps(evidence)
        self.assertIn("resource_crosses_target_boundary", rendered)
        self.assertNotIn(forbidden, rendered)
        self.assertIn("sha256:", rendered)

    def test_permission_sensitive_limit_is_enforced(self):
        payload = self.plan(
            {
                "resources.jobs.dev_loader.permissions": {
                    "action": "create",
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = self.evaluate(
                Path(directory),
                payload,
                max_permission_sensitive_resources=0,
            )
        categories = {item["category"] for item in evidence["findings"]}
        self.assertIn("permission_resource_count_exceeds_policy", categories)

    def test_sensitive_values_must_be_redacted(self):
        secret = "never-persist-this"
        raw = self.plan(
            {
                "resources.jobs.dev_loader": {
                    "action": "create",
                    "new_state": {"client_secret": secret},
                }
            }
        )
        redacted = self.plan(
            {
                "resources.jobs.dev_loader": {
                    "action": "create",
                    "new_state": {"client_secret": "***"},
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocked = self.evaluate(root, raw)
            accepted = self.evaluate(root, redacted)
        self.assertEqual("blocked", blocked["status"])
        self.assertNotIn(secret, json.dumps(blocked))
        self.assertEqual("accepted", accepted["status"])

    def test_plan_schema_version_and_actions_fail_closed(self):
        cases = [
            ({"plan_version": 1, "cli_version": "x", "plan": {}}, "plan_version"),
            (
                self.plan({"resources.jobs.dev": {"action": "replace"}}),
                "plan_entry_action_is_unsupported",
            ),
            (
                {**self.plan({}), "unexpected": True},
                "plan_shape_is_invalid",
            ),
            (
                self.plan(
                    {
                        "resources.jobs.dev": {
                            "action": "update",
                            "changes": {
                                "name": {
                                    "action": "resize",
                                    "old": 1,
                                    "new": 2,
                                }
                            },
                        }
                    }
                ),
                "entry_action_does_not_match_changes",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = review.load_policy(self.write_policy(root), "dev")
            for index, (payload, category) in enumerate(cases):
                with self.subTest(category=category):
                    path = root / f"plan-{index}.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(review.ReviewError, category):
                        review.parse_plan(path, policy=policy)

    def test_policy_shape_and_unknown_target_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self.write_policy(root)
            with self.assertRaisesRegex(
                review.ReviewError,
                "review_target_is_not_configured",
            ):
                review.load_policy(policy, "prod")
            policy.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                review.ReviewError,
                "review_policy_shape_is_invalid",
            ):
                review.load_policy(policy, "dev")

    def test_evidence_is_sanitized_and_symlink_output_is_rejected(self):
        address = "resources.jobs.dev_sensitive_name"
        payload = self.plan({address: {"action": "create"}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self.evaluate(root, payload)
            output = root / "evidence"
            review.write_evidence(output, evidence)
            rendered = (
                (output / "databricks-plan-review.json").read_text()
                + (output / "databricks-plan-review.md").read_text()
            )
            self.assertNotIn(address, rendered)
            symlink = root / "linked-output"
            symlink.symlink_to(output, target_is_directory=True)
            with self.assertRaisesRegex(
                review.ReviewError,
                "output_directory_is_symlink",
            ):
                review.write_evidence(symlink, evidence)

    def test_json_depth_and_source_commit_are_bounded(self):
        nested: dict[str, object] = {}
        cursor = nested
        for _ in range(review.MAX_JSON_DEPTH + 2):
            child: dict[str, object] = {}
            cursor["next"] = child
            cursor = child
        payload = self.plan(
            {
                "resources.jobs.dev": {
                    "action": "create",
                    "new_state": nested,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = review.load_policy(self.write_policy(root), "dev")
            path = root / "plan.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(review.ReviewError, "depth"):
                review.parse_plan(path, policy=policy)

            valid_path = root / "valid.json"
            valid_path.write_text(json.dumps(self.plan({})), encoding="utf-8")
            parsed = review.parse_plan(valid_path, policy=policy)
            with self.assertRaisesRegex(
                review.ReviewError,
                "source_commit_is_invalid",
            ):
                review.review_plan(
                    parsed,
                    policy=policy,
                    target="dev",
                    source_commit="not-a-sha",
                )


if __name__ == "__main__":
    unittest.main()
