from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "validate_operational_observability.py"
SPEC = importlib.util.spec_from_file_location("validate_operational_observability", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

POLICY = REPO_ROOT / "governance" / "operational_alert_policy.json"
SQL = REPO_ROOT / "sql" / "operational_health.sql"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "operational_health.md"


class OperationalObservabilityTest(unittest.TestCase):
    def test_policy_and_assets_are_complete(self):
        policy = validator.load_policy(POLICY)
        result = validator.validate_assets(policy)

        self.assertEqual("valid", result["status"])
        self.assertEqual("policy_only", result["delivery_state"])
        self.assertGreaterEqual(result["alert_count"], 5)
        self.assertGreaterEqual(result["retention_expectation_count"], 5)

    def test_every_alert_has_owner_delay_and_resolved_runbook(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        for alert in policy["alerts"]:
            with self.subTest(alert=alert["id"]):
                self.assertIn(alert["owner"], policy["owners"])
                self.assertIn(alert["severity"], {"warning", "critical"})
                self.assertGreater(alert["maximum_detection_delay_minutes"], 0)
                runbook_path = alert["runbook"].split("#", 1)[0]
                self.assertTrue((REPO_ROOT / runbook_path).is_file())

    def test_sql_is_bounded_and_does_not_claim_delivery(self):
        sql = SQL.read_text(encoding="utf-8")

        self.assertGreaterEqual(sql.count("LIMIT"), 4)
        self.assertIn("policy conditions only", sql)
        self.assertIn("quality_error_check_failed", sql)
        self.assertIn("forecast_publication_failed", sql)
        self.assertIn("forecast_publication_stuck", sql)
        self.assertIn("invalid_ingestion_identity", sql)
        self.assertNotIn("webhook", sql.lower())
        self.assertNotIn("email", sql.lower())

    def test_retention_is_expectation_not_automatic_vacuum(self):
        policy = POLICY.read_text(encoding="utf-8")
        runbook = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("retention_expectations", policy)
        self.assertIn("expectations, not an automatic deletion job", runbook)
        self.assertIn("Before deleting or vacuuming", runbook)
        self.assertNotIn("VACUUM main.lakehouse_demo", SQL.read_text(encoding="utf-8"))

    def test_duplicate_alert_ids_fail_closed(self):
        payload = json.loads(POLICY.read_text(encoding="utf-8"))
        payload["alerts"].append(dict(payload["alerts"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                validator.load_policy(path)

    def test_runbook_preserves_sensitive_data_boundary(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("Do not copy raw telemetry rows", runbook)
        self.assertIn("notification destination identifier", runbook)
        self.assertIn("does not claim that a live notification channel", runbook)


if __name__ == "__main__":
    unittest.main()
