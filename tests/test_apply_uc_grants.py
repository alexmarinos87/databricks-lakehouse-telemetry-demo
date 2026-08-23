from __future__ import annotations

import argparse
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "apply_uc_grants.py"
SPEC = importlib.util.spec_from_file_location("apply_uc_grants", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
apply_uc_grants = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apply_uc_grants)


class ApplyUnityCatalogGrantsTest(unittest.TestCase):
    def test_positive_seconds_rejects_zero_negative_and_non_finite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    apply_uc_grants.positive_seconds(value)

        self.assertEqual(2.5, apply_uc_grants.positive_seconds("2.5"))

    @mock.patch.object(apply_uc_grants.subprocess, "run")
    def test_run_json_uses_subprocess_timeout_and_parses_json(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["databricks"],
            returncode=0,
            stdout='{"state": "ok"}',
            stderr="",
        )

        result = apply_uc_grants.run_json(
            ["databricks", "warehouses", "list"],
            timeout_seconds=12,
        )

        self.assertEqual({"state": "ok"}, result)
        run.assert_called_once_with(
            ["databricks", "warehouses", "list"],
            check=True,
            capture_output=True,
            text=True,
            timeout=12,
        )

    @mock.patch.object(apply_uc_grants.subprocess, "run")
    def test_run_json_does_not_echo_command_or_subprocess_output(self, run):
        run.side_effect = subprocess.CalledProcessError(
            returncode=7,
            cmd=["databricks", "--json", "sensitive-statement"],
            stderr="sensitive-provider-output",
        )

        with self.assertRaisesRegex(RuntimeError, "exit code 7") as raised:
            apply_uc_grants.run_json(
                ["databricks", "--json", "sensitive-statement"],
                timeout_seconds=12,
            )

        message = str(raised.exception)
        self.assertNotIn("sensitive-statement", message)
        self.assertNotIn("sensitive-provider-output", message)

    @mock.patch.object(apply_uc_grants, "run_json")
    def test_execute_statement_polls_to_success_with_bounded_calls(self, run_json):
        run_json.side_effect = [
            {"statement_id": "statement-1", "status": {"state": "PENDING"}},
            {"statement_id": "statement-1", "status": {"state": "RUNNING"}},
            {"statement_id": "statement-1", "status": {"state": "SUCCEEDED"}},
        ]
        monotonic = mock.Mock(side_effect=[0.0, 0.0, 5.0])
        sleep = mock.Mock()

        apply_uc_grants.execute_statement(
            "dev",
            "warehouse-1",
            "main",
            "lakehouse_demo_dev",
            "GRANT SELECT ON TABLE example",
            command_timeout_seconds=10,
            statement_timeout_seconds=30,
            poll_interval_seconds=5,
            monotonic=monotonic,
            sleep=sleep,
        )

        self.assertEqual(3, run_json.call_count)
        self.assertEqual([mock.call(5), mock.call(5)], sleep.call_args_list)
        for call in run_json.call_args_list:
            self.assertEqual(10, call.kwargs["timeout_seconds"])

    @mock.patch.object(apply_uc_grants, "run_json")
    def test_execute_statement_times_out_and_attempts_one_cancel(self, run_json):
        run_json.side_effect = [
            {"statement_id": "statement-1", "status": {"state": "PENDING"}},
            {},
        ]
        monotonic = mock.Mock(side_effect=[0.0, 31.0])
        sleep = mock.Mock()

        with self.assertRaisesRegex(TimeoutError, "exceeded 30 seconds"):
            apply_uc_grants.execute_statement(
                "dev",
                "warehouse-1",
                "main",
                "lakehouse_demo_dev",
                "GRANT SELECT ON TABLE example",
                command_timeout_seconds=10,
                statement_timeout_seconds=30,
                poll_interval_seconds=5,
                monotonic=monotonic,
                sleep=sleep,
            )

        self.assertEqual(2, run_json.call_count)
        cancel_command = run_json.call_args_list[1].args[0]
        self.assertIn("/api/2.0/sql/statements/statement-1/cancel", cancel_command)
        sleep.assert_not_called()

    @mock.patch.object(apply_uc_grants, "run_json")
    def test_execute_statement_failure_omits_sql_and_provider_message(self, run_json):
        run_json.return_value = {
            "statement_id": "statement-1",
            "status": {
                "state": "FAILED",
                "error": {"message": "sensitive-provider-output"},
            },
        }

        with self.assertRaisesRegex(RuntimeError, "finished in FAILED state") as raised:
            apply_uc_grants.execute_statement(
                "prod",
                "warehouse-1",
                "main",
                "lakehouse_demo_prod",
                "GRANT SELECT ON TABLE sensitive_table",
            )

        message = str(raised.exception)
        self.assertNotIn("sensitive_table", message)
        self.assertNotIn("sensitive-provider-output", message)

    def _args(self, **overrides):
        values = {
            "catalog": "main",
            "schema": "lakehouse_demo_dev",
            "volume": "lakehouse_demo_dev_files",
            "admin_group": "admins",
            "engineer_group": "engineers",
            "analyst_group": "analysts",
            "service_principal": "lakehouse-demo-ci",
            "runtime_service_principal": "lakehouse-demo-runtime",
            "include_table_grants": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_relation_specific_select_grants_preserve_forecast_boundaries(self):
        statements = apply_uc_grants.build_grants(self._args())
        joined = "\n".join(statements)

        self.assertIn(
            "GRANT SELECT ON VIEW `main`.`lakehouse_demo_dev`.`gold_downtime_forecast`",
            joined,
        )
        self.assertIn(
            "GRANT SELECT ON VIEW `main`.`lakehouse_demo_dev`.`gold_downtime_forecast_validation`",
            joined,
        )
        self.assertIn(
            "GRANT SELECT ON MATERIALIZED VIEW `main`.`lakehouse_demo_dev`.`quality_expectation_downtime_forecast`",
            joined,
        )
        self.assertIn(
            "GRANT SELECT ON MATERIALIZED VIEW `main`.`lakehouse_demo_dev`.`quality_expectation_forecast_publication_manifest`",
            joined,
        )
        self.assertNotIn("ON TABLE `main`.`lakehouse_demo_dev`.`gold_downtime_forecast`", joined)
        self.assertNotIn("gold_downtime_forecast_history", joined)
        self.assertNotIn("gold_downtime_forecast_validation_history", joined)
        self.assertNotIn("gold_downtime_forecast_publication_manifest", joined)

    def test_runtime_identity_receives_catalog_schema_and_volume_access(self):
        joined = "\n".join(apply_uc_grants.build_grants(self._args()))

        for fragment in (
            "GRANT USE CATALOG ON CATALOG `main` TO `lakehouse-demo-runtime`",
            "GRANT USE SCHEMA ON SCHEMA `main`.`lakehouse_demo_dev` TO `lakehouse-demo-runtime`",
            "GRANT READ VOLUME, WRITE VOLUME ON VOLUME `main`.`lakehouse_demo_dev`.`lakehouse_demo_dev_files` TO `lakehouse-demo-runtime`",
        ):
            self.assertIn(fragment, joined)

    def test_deployment_and_runtime_principals_must_differ(self):
        with self.assertRaisesRegex(ValueError, "must differ"):
            apply_uc_grants.build_grants(
                self._args(runtime_service_principal="lakehouse-demo-ci")
            )


if __name__ == "__main__":
    unittest.main()
