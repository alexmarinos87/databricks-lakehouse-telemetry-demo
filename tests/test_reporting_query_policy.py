from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "upsert_reporting_queries.py"
POLICY_PATH = REPO_ROOT / "governance" / "reporting_query_policy.json"
DOC_PATH = REPO_ROOT / "docs" / "reporting_query_ownership.md"
CHANGE_BRIEF = (
    REPO_ROOT / "docs" / "change_briefs" / "govern_reporting_query_ownership.md"
)
SPEC = importlib.util.spec_from_file_location("upsert_reporting_queries_policy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
publisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)


class ReportingQueryPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = publisher.load_policy(POLICY_PATH)

    def test_policy_is_viewer_run_and_least_privilege(self):
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

        self.assertEqual("repository", payload["source_of_truth"])
        self.assertEqual("VIEWER", payload["execution_mode"])
        self.assertEqual("admin_only", payload["workspace_editing"])
        self.assertFalse(payload["owner_credentials_used_for_execution"])
        self.assertEqual("manual_workspace_admin", payload["ownership_transfer"])
        self.assertEqual(
            {
                "admin_group": "CAN_MANAGE",
                "engineer_group": "CAN_RUN",
                "analyst_group": "CAN_RUN",
                "publisher_service_principal": "CAN_MANAGE",
            },
            payload["permissions"],
        )
        self.assertNotIn("CAN_EDIT", json.dumps(payload, sort_keys=True))
        self.assertNotIn('"OWNER"', json.dumps(payload, sort_keys=True))

    def test_policy_rejects_owner_run_or_editor_grants(self):
        original = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        for mutation, pattern in (
            (("execution_mode", "OWNER"), "execution_mode"),
            (("permissions.engineer_group", "CAN_EDIT"), "least privilege"),
        ):
            with self.subTest(mutation=mutation):
                candidate = json.loads(json.dumps(original))
                dotted_key, value = mutation
                if "." in dotted_key:
                    section, key = dotted_key.split(".", 1)
                    candidate[section][key] = value
                else:
                    candidate[dotted_key] = value
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "policy.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, pattern):
                        publisher.load_policy(path)

    @mock.patch.object(publisher, "run_json")
    def test_publisher_identity_must_match_application_id(self, run_json):
        run_json.return_value = {"application_id": "publisher-app"}

        publisher.verify_publisher_identity(
            "dev",
            "publisher-app",
            command_timeout_seconds=11,
        )

        self.assertEqual(11, run_json.call_args.kwargs["timeout_seconds"])
        self.assertIn("current-user", run_json.call_args.args[0])
        run_json.return_value = {"application_id": "other-app"}
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            publisher.verify_publisher_identity("dev", "publisher-app")

    def test_query_payload_is_viewer_run_and_repository_tagged(self):
        payload = publisher.query_payload(
            target="dev",
            display_name="DEV governed report",
            description="description",
            query_text="SELECT 1",
            warehouse_id="warehouse-1",
            catalog="main",
            schema="demo",
            parent_path="/Shared/demo",
            policy=self.policy,
        )["query"]

        self.assertEqual("VIEWER", payload["run_as_mode"])
        self.assertTrue(
            {"lakehouse-demo", "repository-managed", "viewer-run", "dev"}.issubset(
                payload["tags"]
            )
        )

    def test_query_readback_rejects_owner_run_or_text_drift(self):
        expected = {
            "display_name": "DEV report",
            "description": "description",
            "parent_path": "/Shared/demo",
            "query_text": "SELECT 1",
            "warehouse_id": "warehouse-1",
            "catalog": "main",
            "schema": "demo",
            "run_as_mode": "VIEWER",
            "tags": ["lakehouse-demo", "repository-managed", "viewer-run", "dev"],
        }
        publisher.verify_query_definition(
            expected,
            target="dev",
            display_name="DEV report",
            description="description",
            query_text="SELECT 1",
            warehouse_id="warehouse-1",
            catalog="main",
            schema="demo",
            parent_path="/Shared/demo",
            policy=self.policy,
        )

        owner_run = {**expected, "run_as_mode": "OWNER"}
        with self.assertRaisesRegex(RuntimeError, "repository state|owner-run"):
            publisher.verify_query_definition(
                owner_run,
                target="dev",
                display_name="DEV report",
                description="description",
                query_text="SELECT 1",
                warehouse_id="warehouse-1",
                catalog="main",
                schema="demo",
                parent_path="/Shared/demo",
                policy=self.policy,
            )

        drifted = {**expected, "query_text": "SELECT sensitive_column"}
        with self.assertRaisesRegex(RuntimeError, "repository state"):
            publisher.verify_query_definition(
                drifted,
                target="dev",
                display_name="DEV report",
                description="description",
                query_text="SELECT 1",
                warehouse_id="warehouse-1",
                catalog="main",
                schema="demo",
                parent_path="/Shared/demo",
                policy=self.policy,
            )

    def permission_response(self):
        return {
            "access_control_list": [
                {
                    "group_name": "admins",
                    "all_permissions": [
                        {"permission_level": "CAN_MANAGE", "inherited": False}
                    ],
                },
                {
                    "group_name": "engineers",
                    "all_permissions": [
                        {"permission_level": "CAN_RUN", "inherited": False}
                    ],
                },
                {
                    "group_name": "analysts",
                    "all_permissions": [
                        {"permission_level": "CAN_RUN", "inherited": False}
                    ],
                },
                {
                    "service_principal_name": "publisher",
                    "all_permissions": [
                        {"permission_level": "CAN_MANAGE", "inherited": False}
                    ],
                },
                {
                    "user_name": "administrative-owner@example.com",
                    "all_permissions": [
                        {"permission_level": "CAN_MANAGE", "inherited": False}
                    ],
                },
            ]
        }

    def verify_permissions(self, payload):
        publisher.verify_query_permissions(
            payload,
            admin_group="admins",
            engineer_group="engineers",
            analyst_group="analysts",
            service_principal="publisher",
            policy=self.policy,
        )

    def test_permission_readback_accepts_policy_and_administrative_owner(self):
        self.verify_permissions(self.permission_response())

        request = publisher.permission_payload(
            admin_group="admins",
            engineer_group="engineers",
            analyst_group="analysts",
            service_principal="publisher",
            policy=self.policy,
        )
        levels = {
            entry.get("group_name") or entry.get("service_principal_name"): entry[
                "permission_level"
            ]
            for entry in request["access_control_list"]
        }
        self.assertEqual("CAN_RUN", levels["engineers"])
        self.assertEqual("CAN_RUN", levels["analysts"])
        self.assertNotIn("CAN_EDIT", levels.values())

    def test_permission_readback_rejects_elevated_humans(self):
        payload = self.permission_response()
        payload["access_control_list"][1]["all_permissions"].append(
            {"permission_level": "CAN_EDIT", "inherited": False}
        )
        with self.assertRaisesRegex(RuntimeError, "elevated human access"):
            self.verify_permissions(payload)

    def test_permission_readback_rejects_unexpected_explicit_principals(self):
        payload = self.permission_response()
        payload["access_control_list"].append(
            {
                "group_name": "unexpected-editors",
                "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": False}
                ],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "unexpected principal"):
            self.verify_permissions(payload)

        inherited = self.permission_response()
        inherited["access_control_list"].append(
            {
                "group_name": "workspace-users",
                "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": True}
                ],
            }
        )
        self.verify_permissions(inherited)

    def test_source_orders_identity_definition_and_acl_verification(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        main_source = source[source.index("def main()") :]

        identity = main_source.index("verify_publisher_identity(")
        warehouse = main_source.index("find_warehouse_id(")
        upsert = main_source.index("query_id = upsert_query(")
        readback = main_source.index("query = get_query(")
        query_verify = main_source.index("verify_query_definition(")
        acl_set = main_source.index("apply_query_permissions(")
        acl_get = main_source.index("permissions = get_query_permissions(")
        acl_verify = main_source.index("verify_query_permissions(")

        self.assertLess(identity, warehouse)
        self.assertLess(warehouse, upsert)
        self.assertLess(upsert, readback)
        self.assertLess(readback, query_verify)
        self.assertLess(query_verify, acl_set)
        self.assertLess(acl_set, acl_get)
        self.assertLess(acl_get, acl_verify)
        self.assertIn('"run_as_mode": policy.execution_mode', source)
        self.assertNotIn('"run_as_mode": "OWNER"', source)

    def test_docs_define_migration_ownership_and_rollback_boundaries(self):
        documentation = DOC_PATH.read_text(encoding="utf-8")
        brief = CHANGE_BRIEF.read_text(encoding="utf-8")
        for source in (documentation, brief):
            with self.subTest(source=source[:20]):
                self.assertIn("VIEWER", source)
                self.assertIn("CAN_RUN", source)
                self.assertIn("ownership", source.lower())
                self.assertIn("migration", source.lower())
                self.assertIn("rollback", source.lower())
                self.assertIn("repository", source.lower())
        self.assertIn("Do not grant editor access", documentation)
        self.assertIn("does not transfer a live query owner", brief)


if __name__ == "__main__":
    unittest.main()
