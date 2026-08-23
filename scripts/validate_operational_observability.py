#!/usr/bin/env python3
"""Validate operational alert, retention, SQL, and runbook coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "governance" / "operational_alert_policy.json"
SQL_PATH = REPO_ROOT / "sql" / "operational_health.sql"
RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "operational_health.md"

_REQUIRED_TOP_LEVEL = {
    "schema_version",
    "owners",
    "retention_expectations",
    "alerts",
    "delivery",
}
_REQUIRED_ALERT_KEYS = {
    "id",
    "severity",
    "owner",
    "condition",
    "maximum_detection_delay_minutes",
    "runbook",
}
_ALLOWED_SEVERITIES = {"warning", "critical"}
_REQUIRED_RETENTION = {
    "quality_check_results_days",
    "quality_metric_history_days",
    "forecast_history_days",
    "forecast_publication_manifest_days",
    "expectation_event_log_days",
}


def load_policy(path: Path = POLICY_PATH) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("operational alert policy could not be loaded") from None
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_TOP_LEVEL:
        raise ValueError("operational alert policy has an invalid top-level shape")
    if payload["schema_version"] != 1:
        raise ValueError("operational alert policy schema version is unsupported")
    if not isinstance(payload["owners"], dict) or not payload["owners"]:
        raise ValueError("operational alert policy must define owners")
    retention = payload["retention_expectations"]
    if not isinstance(retention, dict) or set(retention) != _REQUIRED_RETENTION:
        raise ValueError("retention expectations have an invalid shape")
    for name, days in retention.items():
        if not isinstance(days, int) or isinstance(days, bool) or days < 30 or days > 3650:
            raise ValueError(f"retention expectation {name} is outside the bounded range")
    alerts = payload["alerts"]
    if not isinstance(alerts, list) or not alerts or len(alerts) > 50:
        raise ValueError("alert policy must define a bounded non-empty alert list")
    seen_ids: set[str] = set()
    for alert in alerts:
        if not isinstance(alert, dict) or set(alert) != _REQUIRED_ALERT_KEYS:
            raise ValueError("alert definition has an invalid shape")
        alert_id = alert["id"]
        if not isinstance(alert_id, str) or not alert_id or alert_id in seen_ids:
            raise ValueError("alert IDs must be unique non-empty strings")
        seen_ids.add(alert_id)
        if alert["severity"] not in _ALLOWED_SEVERITIES:
            raise ValueError(f"alert {alert_id} has an unsupported severity")
        if alert["owner"] not in payload["owners"]:
            raise ValueError(f"alert {alert_id} refers to an unknown owner")
        delay = alert["maximum_detection_delay_minutes"]
        if not isinstance(delay, int) or isinstance(delay, bool) or delay < 1 or delay > 1440:
            raise ValueError(f"alert {alert_id} has an invalid detection delay")
        runbook = alert["runbook"]
        if not isinstance(runbook, str) or not runbook.startswith("docs/"):
            raise ValueError(f"alert {alert_id} has an invalid runbook link")
    delivery = payload["delivery"]
    if not isinstance(delivery, dict) or delivery.get("repository_state") != "policy_only":
        raise ValueError("delivery boundary must remain policy_only in the repository")
    external = delivery.get("required_external_evidence")
    if not isinstance(external, list) or not external:
        raise ValueError("delivery boundary must define external evidence")
    return payload


def validate_assets(policy: Mapping[str, Any]) -> dict[str, Any]:
    sql = SQL_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    normalized_runbook = " ".join(runbook.lower().split())
    for alert in policy["alerts"]:
        if alert["id"] not in sql and alert["id"] != "deployment_or_runtime_identity_mismatch":
            raise ValueError(f"SQL does not expose alert candidate {alert['id']}")
        runbook_path = alert["runbook"].split("#", 1)[0]
        if not (REPO_ROOT / runbook_path).is_file():
            raise ValueError(f"runbook path does not exist for alert {alert['id']}")
    for token in (
        "quality_metric_history",
        "gold_downtime_forecast_publication_manifest",
        "bronze_machine_events",
        "LIMIT 100",
        "policy conditions only",
    ):
        if token not in sql:
            raise ValueError(f"operational SQL is missing required token: {token}")
    if "does not claim that a live notification channel" not in normalized_runbook:
        raise ValueError("runbook must preserve the alert delivery evidence boundary")
    return {
        "schema_version": policy["schema_version"],
        "owner_count": len(policy["owners"]),
        "alert_count": len(policy["alerts"]),
        "retention_expectation_count": len(policy["retention_expectations"]),
        "delivery_state": policy["delivery"]["repository_state"],
        "status": "valid",
    }


def main() -> int:
    print(json.dumps(validate_assets(load_policy()), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
