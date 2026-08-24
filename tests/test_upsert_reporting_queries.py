from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "upsert_reporting_queries.py"
SPEC = importlib.util.spec_from_file_location("upsert_reporting_queries", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
upsert_reporting_queries = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = upsert_reporting_queries
SPEC.loader.exec_module(upsert_reporting_queries)


class UpsertReportingQueriesTest(unittest.TestCase):
    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    upsert_reporting_queries.positive_seconds(value)
        self.assertEqual(2.5, upsert_reporting_queries.positive_seconds("2.5"))

    @mock.patch.object(upsert_reporting_queries.subprocess, "run")
    def test_run_json_uses_timeout_and_does_not_echo_sensitive_failure(self, run):
        run.side_effect = subprocess.CalledProcessError(
            9,
            ["databricks", "--json", "sensitive-query"],
            stderr="sensitive-provider-output",
        )
        with self.assertRaisesRegex(RuntimeError, "exit code 9") as raised:
            upsert_reporting_queries.run_json(
                ["databricks", "--json", "sensitive-query"],
                timeout_seconds=12,
            )
        self.assertNotIn("sensitive-query", str(raised.exception))
        self.assertNotIn("sensitive-provider-output", str(raised.exception))
        run.assert_called_once_with(
            ["databricks", "--json", "sensitive-query"],
            check=True,
            capture_output=True,
            text=True,
            timeout=12,
        )

    @mock.patch.object(upsert_reporting_queries, "run_json")
    def test_query_inventory_paginates_with_bounded_cli_calls(self, run_json):
        run_json.side_effect = [
            {
                "results": [{"id": "query-1", "display_name": "one"}],
                "next_page_token": "page-2",
            },
            {"results": [{"id": "query-2", "display_name": "two"}]},
        ]

        queries = upsert_reporting_queries.list_queries(
            "dev", command_timeout_seconds=13
        )

        self.assertEqual(["query-1", "query-2"], [query["id"] for query in queries])
        self.assertEqual(2, run_json.call_count)
        first_command = run_json.call_args_list[0].args[0]
        second_command = run_json.call_args_list[1].args[0]
        self.assertIn("--page-size", first_command)
        self.assertNotIn("--page-token", first_command)
        self.assertEqual(
            "page-2", second_command[second_command.index("--page-token") + 1]
        )
        for call in run_json.call_args_list:
            self.assertEqual(13, call.kwargs["timeout_seconds"])

    @mock.patch.object(upsert_reporting_queries, "run_json")
    def test_query_inventory_repeated_page_token_fails_closed(self, run_json):
        run_json.side_effect = [
            {"results": [], "next_page_token": "repeated"},
            {"results": [], "next_page_token": "repeated"},
        ]

        with self.assertRaisesRegex(RuntimeError, "repeated a page token"):
            upsert_reporting_queries.list_queries("prod")

    @mock.patch.object(upsert_reporting_queries, "run")
    def test_existing_query_update_is_bounded_and_uses_explicit_mask(self, run):
        query_id = upsert_reporting_queries.upsert_query(
            target="dev",
            display_name="DEV report",
            description="description",
            query_text="SELECT 1",
            warehouse_id="warehouse-1",
            catalog="main",
            schema="demo",
            parent_path="/Shared/demo",
            existing_queries=[
                {
                    "id": "query-1",
                    "display_name": "DEV report",
                    "lifecycle_state": "ACTIVE",
                }
            ],
            command_timeout_seconds=14,
        )
        self.assertEqual("query-1", query_id)
        self.assertEqual(14, run.call_args.kwargs["timeout_seconds"])
        command = run.call_args.args[0]
        self.assertIn("update", command)
        self.assertIn(upsert_reporting_queries._QUERY_UPDATE_MASK, command)

    @mock.patch.object(upsert_reporting_queries, "run_json")
    def test_new_query_creation_is_bounded(self, run_json):
        run_json.return_value = {"id": "query-2"}
        query_id = upsert_reporting_queries.upsert_query(
            target="prod",
            display_name="PROD report",
            description="description",
            query_text="SELECT 1",
            warehouse_id="warehouse-1",
            catalog="main",
            schema="demo",
            parent_path="/Shared/demo",
            existing_queries=[],
            command_timeout_seconds=15,
        )
        self.assertEqual("query-2", query_id)
        self.assertEqual(15, run_json.call_args.kwargs["timeout_seconds"])
        self.assertIn("create", run_json.call_args.args[0])

    def test_duplicate_active_display_names_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Multiple active queries"):
            upsert_reporting_queries.find_active_query_id(
                [
                    {
                        "id": "q1",
                        "display_name": "DEV report",
                        "lifecycle_state": "ACTIVE",
                    },
                    {
                        "id": "q2",
                        "display_name": "DEV report",
                        "lifecycle_state": "ACTIVE",
                    },
                ],
                "DEV report",
            )

    @mock.patch.object(upsert_reporting_queries, "run")
    def test_permission_update_is_bounded(self, run):
        upsert_reporting_queries.apply_query_permissions(
            target="dev",
            query_id="query-1",
            admin_group="admins",
            engineer_group="engineers",
            analyst_group="analysts",
            service_principal="ci",
            command_timeout_seconds=16,
        )
        self.assertEqual(16, run.call_args.kwargs["timeout_seconds"])
        command = run.call_args.args[0]
        self.assertIn("permissions", command)
        payload = json.loads(command[-1])
        self.assertEqual(4, len(payload["access_control_list"]))

    def test_manifest_rejects_path_traversal_and_duplicate_display_names(self):
        with tempfile.TemporaryDirectory() as directory:
            asset_dir = Path(directory)
            (asset_dir / "one.sql").write_text("SELECT 1", encoding="utf-8")
            manifest = asset_dir / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "display_name": "same",
                            "description": "one",
                            "file": "one.sql",
                        },
                        {
                            "display_name": "same",
                            "description": "two",
                            "file": "../two.sql",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(upsert_reporting_queries, "MANIFEST", manifest),
                mock.patch.object(upsert_reporting_queries, "ASSET_DIR", asset_dir),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "duplicate display name|unsafe"
                ):
                    upsert_reporting_queries.load_assets()

    def test_manifest_rejects_oversized_sql_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            asset_dir = Path(directory)
            (asset_dir / "large.sql").write_bytes(
                b"x" * (upsert_reporting_queries.MAX_ASSET_BYTES + 1)
            )
            manifest = asset_dir / "manifest.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "display_name": "large",
                            "description": "large",
                            "file": "large.sql",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(upsert_reporting_queries, "MANIFEST", manifest),
                mock.patch.object(upsert_reporting_queries, "ASSET_DIR", asset_dir),
            ):
                with self.assertRaisesRegex(RuntimeError, "size limit"):
                    upsert_reporting_queries.load_assets()


if __name__ == "__main__":
    unittest.main()
