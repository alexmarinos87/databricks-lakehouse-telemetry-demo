#!/usr/bin/env python3
"""Validate the repository-owned deployment/runtime identity contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "governance" / "runtime_identity_policy.json"

_REQUIRED_IDENTITIES = {"deployer", "runtime"}
_REQUIRED_IDENTITY_KEYS = {
    "bundle_variable",
    "github_variable",
    "allowed_capabilities",
    "expected_denied_capabilities",
}
_REQUIRED_EVIDENCE_KEYS = {
    "repository_contract",
    "authenticated_plan",
    "effective_permission_export",
    "denied_capability_review",
    "static_client_secret",
}


def load_policy(path: Path = POLICY_PATH) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("runtime identity policy could not be loaded") from None
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "identities",
        "evidence",
    }:
        raise ValueError("runtime identity policy has an invalid top-level shape")
    if payload["schema_version"] != 1:
        raise ValueError("runtime identity policy schema version is unsupported")
    identities = payload["identities"]
    if not isinstance(identities, dict) or set(identities) != _REQUIRED_IDENTITIES:
        raise ValueError("runtime identity policy must define deployer and runtime")
    for name, identity in identities.items():
        if not isinstance(identity, dict) or set(identity) != _REQUIRED_IDENTITY_KEYS:
            raise ValueError(f"{name} identity policy has an invalid shape")
        for key in ("bundle_variable", "github_variable"):
            if not isinstance(identity[key], str) or not identity[key]:
                raise ValueError(f"{name} identity has an invalid {key}")
        for key in ("allowed_capabilities", "expected_denied_capabilities"):
            values = identity[key]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))
            ):
                raise ValueError(f"{name} identity has invalid {key}")
    evidence = payload["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != _REQUIRED_EVIDENCE_KEYS:
        raise ValueError("runtime identity evidence policy has an invalid shape")
    return payload


def validate_repository(policy: Mapping[str, Any]) -> dict[str, Any]:
    bundle = (REPO_ROOT / "databricks.yml").read_text(encoding="utf-8")
    job = (REPO_ROOT / "resources" / "lakehouse_workflow.yml").read_text(
        encoding="utf-8"
    )
    pipeline = (
        REPO_ROOT / "resources" / "lakehouse_quality_expectations.yml"
    ).read_text(encoding="utf-8")
    grants = (REPO_ROOT / "resources" / "access_controls.yml").read_text(
        encoding="utf-8"
    )
    deploy = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    deployer_variable = policy["identities"]["deployer"]["bundle_variable"]
    runtime_variable = policy["identities"]["runtime"]["bundle_variable"]
    if deployer_variable == runtime_variable:
        raise ValueError("deployment and runtime bundle variables must be distinct")
    for variable in (deployer_variable, runtime_variable):
        if f"  {variable}:\n" not in bundle:
            raise ValueError(f"bundle is missing identity variable {variable}")
    run_as_token = f"service_principal_name: ${{var.{runtime_variable}}}"
    if run_as_token not in job or run_as_token not in pipeline:
        raise ValueError("job and pipeline must use the runtime run-as identity")
    if f"service_principal_name: ${{var.{deployer_variable}}}\n          level: CAN_MANAGE" not in job:
        raise ValueError("deployer must retain job management access")
    if f"service_principal_name: ${{var.{deployer_variable}}}\n          level: CAN_MANAGE" not in pipeline:
        raise ValueError("deployer must retain pipeline management access")
    if "- principal: ${var.runtime_service_principal_name}" not in grants:
        raise ValueError("runtime Unity Catalog grants are missing")
    if "DATABRICKS_RUNTIME_CLIENT_ID" not in deploy:
        raise ValueError("deployment workflow is missing the runtime client ID")
    if deploy.count(
        '--bundle-var "runtime_service_principal_name=${RUNTIME_SERVICE_PRINCIPAL_NAME}"'
    ) != 2:
        raise ValueError("plan jobs must bind the runtime principal explicitly")
    if deploy.count(
        '--var="runtime_service_principal_name=${RUNTIME_SERVICE_PRINCIPAL_NAME}"'
    ) != 4:
        raise ValueError("deploy and run commands must bind the runtime principal")
    if deploy.count("Verify dev runtime upload identity") != 1 or deploy.count(
        "Verify prod runtime upload identity"
    ) != 1:
        raise ValueError("runtime upload identity preflights are missing")
    if "DATABRICKS_CLIENT_SECRET" in deploy:
        raise ValueError("static Databricks client secret mapping is prohibited")

    return {
        "schema_version": policy["schema_version"],
        "deployer_bundle_variable": deployer_variable,
        "runtime_bundle_variable": runtime_variable,
        "run_as_resources": 2,
        "runtime_identity_preflights": 2,
        "status": "valid",
    }


def main() -> int:
    result = validate_repository(load_policy())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
