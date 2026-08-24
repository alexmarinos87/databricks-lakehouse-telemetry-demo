#!/usr/bin/env python3
"""Dry-run-first bootstrap for Databricks service-principal federation policies."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ISSUER = "https://token.actions.githubusercontent.com"
REQUIRED_ENVIRONMENTS = ("dev-plan", "prod-plan", "dev", "prod")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NUMERIC_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")


@dataclass(frozen=True)
class PrincipalConfig:
    numeric_id: str
    application_id: str


@dataclass(frozen=True)
class FederationConfig:
    repository: str
    account_host: str
    account_id: str
    audience: str
    principals: Mapping[str, PrincipalConfig]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def load_config(path: str | Path) -> FederationConfig:
    config_path = Path(path)
    try:
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("federation config must be a regular file")
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("federation config could not be parsed") from None
    expected = {"repository", "account_host", "account_id", "audience", "principals"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("federation config has an invalid top-level shape")
    repository = payload["repository"]
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("repository must use owner/name form")
    strings = (payload["account_host"], payload["account_id"], payload["audience"])
    if not all(isinstance(value, str) and value.strip() for value in strings):
        raise ValueError("account host, account ID, and audience must be populated")
    if not payload["account_host"].startswith("https://"):
        raise ValueError("account host must be an https URL")
    raw_principals = payload["principals"]
    if not isinstance(raw_principals, dict) or set(raw_principals) != set(REQUIRED_ENVIRONMENTS):
        raise ValueError("federation config must define the four required environments")
    principals: dict[str, PrincipalConfig] = {}
    numeric_to_application: dict[str, str] = {}
    for environment in REQUIRED_ENVIRONMENTS:
        raw = raw_principals[environment]
        if not isinstance(raw, dict) or set(raw) != {"numeric_id", "application_id"}:
            raise ValueError(f"principal {environment} has an invalid shape")
        numeric_id = raw["numeric_id"]
        application_id = raw["application_id"]
        if not isinstance(numeric_id, str) or not _NUMERIC_ID_PATTERN.fullmatch(numeric_id):
            raise ValueError(f"principal {environment} numeric ID is invalid")
        if (not isinstance(application_id, str) or not application_id.strip()
                or len(application_id.strip()) > 256
                or any(character.isspace() for character in application_id.strip())):
            raise ValueError(f"principal {environment} application ID is invalid")
        application_id = application_id.strip()
        if numeric_id in numeric_to_application and numeric_to_application[numeric_id] != application_id:
            raise ValueError("one numeric service-principal ID maps to conflicting application IDs")
        numeric_to_application[numeric_id] = application_id
        principals[environment] = PrincipalConfig(numeric_id, application_id)
    return FederationConfig(
        repository=repository,
        account_host=payload["account_host"].strip().rstrip("/"),
        account_id=payload["account_id"].strip(),
        audience=payload["audience"].strip(),
        principals=principals,
    )


def subject(repository: str, environment: str) -> str:
    if environment not in REQUIRED_ENVIRONMENTS:
        raise ValueError("unsupported GitHub environment")
    return f"repo:{repository}:environment:{environment}"


def policy_payload(config: FederationConfig, environment: str) -> dict[str, Any]:
    return {"oidc_policy": {
        "issuer": ISSUER,
        "audiences": [config.audience],
        "subject": subject(config.repository, environment),
    }}


class DatabricksCli:
    def __init__(self, config: FederationConfig, *, timeout_seconds: float = 60.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout must be finite and positive")
        self._timeout_seconds = timeout_seconds
        self._environment = {
            **os.environ,
            "DATABRICKS_HOST": config.account_host,
            "DATABRICKS_ACCOUNT_ID": config.account_id,
        }

    def json(self, command: list[str]) -> Any:
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True,
                                       timeout=self._timeout_seconds, env=self._environment)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Databricks CLI command exceeded {self._timeout_seconds:g} seconds") from None
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Databricks CLI command failed with exit code {exc.returncode}") from None
        except OSError:
            raise RuntimeError("Databricks CLI command could not be started") from None
        try:
            return json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            raise RuntimeError("Databricks CLI returned invalid JSON") from None


def _policies(payload: Any) -> list[dict[str, Any]]:
    items: Any = payload
    if isinstance(payload, dict):
        items = next((payload[key] for key in ("policies", "items", "results") if key in payload), None)
    if not isinstance(items, list):
        raise RuntimeError("Databricks federation-policy list response is malformed")
    if not all(isinstance(item, dict) for item in items):
        raise RuntimeError("Databricks federation-policy list contains a malformed item")
    return list(items)


def _matching_subject(policies: list[dict[str, Any]], expected_subject: str) -> list[dict[str, Any]]:
    return [policy for policy in policies
            if isinstance(policy.get("oidc_policy"), dict)
            and policy["oidc_policy"].get("subject") == expected_subject]


def _application_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError("Databricks service-principal response is malformed")
    for key in ("applicationId", "application_id"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()
    raise RuntimeError("Databricks service-principal response has no application ID")


def _unique_principals(config: FederationConfig) -> dict[str, PrincipalConfig]:
    result: dict[str, PrincipalConfig] = {}
    for principal in config.principals.values():
        result.setdefault(principal.numeric_id, principal)
    return result


def _inventory(config: FederationConfig, cli: DatabricksCli) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for numeric_id, principal in _unique_principals(config).items():
        actual = cli.json([
            "databricks", "account", "service-principals", "get",
            numeric_id, "-o", "json",
        ])
        if _application_id(actual) != principal.application_id:
            raise RuntimeError(
                "Databricks numeric service-principal ID does not match the configured application ID")
        if not isinstance(actual, dict) or actual.get("active") is not True:
            raise RuntimeError("configured Databricks service principal is not active")
        result[numeric_id] = _policies(cli.json([
            "databricks", "account", "service-principal-federation-policy", "list",
            numeric_id, "-o", "json",
        ]))
    return result


def _locations(inventory: Mapping[str, list[dict[str, Any]]],
               expected_subject: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (numeric_id, policy)
        for numeric_id, policies in inventory.items()
        for policy in _matching_subject(policies, expected_subject)
    ]


def _verify_policy(policy: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    actual = policy.get("oidc_policy")
    if (not isinstance(actual, dict)
            or actual.get("issuer") != expected["issuer"]
            or actual.get("audiences") != expected["audiences"]
            or actual.get("subject") != expected["subject"]):
        raise RuntimeError(
            "existing Databricks federation policy conflicts with the requested policy")


def verify_policies(config: FederationConfig, *, cli: DatabricksCli) -> dict[str, Any]:
    inventory = _inventory(config, cli)
    results = []
    for environment, principal in config.principals.items():
        expected = policy_payload(config, environment)["oidc_policy"]
        locations = _locations(inventory, expected["subject"])
        if not locations:
            raise RuntimeError("required Databricks federation policy is missing")
        if len(locations) > 1:
            raise RuntimeError("multiple Databricks federation policies share one subject")
        numeric_id, policy = locations[0]
        if numeric_id != principal.numeric_id:
            raise RuntimeError(
                "Databricks federation subject is attached to an unexpected principal")
        _verify_policy(policy, expected)
        results.append({
            "environment": environment,
            "outcome": "verified",
            "numeric_id_fingerprint": _fingerprint(principal.numeric_id),
            "application_id_fingerprint": _fingerprint(principal.application_id),
            "subject_fingerprint": _fingerprint(expected["subject"]),
        })
    return {
        "mode": "verify",
        "repository": config.repository,
        "issuer": ISSUER,
        "account_host_fingerprint": _fingerprint(config.account_host),
        "account_id_fingerprint": _fingerprint(config.account_id),
        "audience_fingerprint": _fingerprint(config.audience),
        "policies": results,
        "write_operations": 0,
    }


def ensure_policies(config: FederationConfig, *, cli: DatabricksCli) -> dict[str, Any]:
    inventory = _inventory(config, cli)
    outcomes: dict[str, str] = {}
    for environment, principal in config.principals.items():
        expected = policy_payload(config, environment)["oidc_policy"]
        locations = _locations(inventory, expected["subject"])
        if len(locations) > 1:
            raise RuntimeError("multiple Databricks federation policies share one subject")
        if locations:
            numeric_id, policy = locations[0]
            if numeric_id != principal.numeric_id:
                raise RuntimeError(
                    "Databricks federation subject is attached to an unexpected principal")
            _verify_policy(policy, expected)
            outcomes[environment] = "verified"
        else:
            cli.json([
                "databricks", "account", "service-principal-federation-policy", "create",
                principal.numeric_id, "--json",
                json.dumps(policy_payload(config, environment), sort_keys=True), "-o", "json",
            ])
            outcomes[environment] = "created"
    verified = verify_policies(config, cli=cli)
    return {
        **verified,
        "mode": "apply",
        "read_back_verified": True,
        "policies": [
            {**item, "outcome": outcomes[item["environment"]]}
            for item in verified["policies"]
        ],
    }


def dry_run_summary(config: FederationConfig) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "repository": config.repository,
        "account_host_fingerprint": _fingerprint(config.account_host),
        "account_id_fingerprint": _fingerprint(config.account_id),
        "audience_fingerprint": _fingerprint(config.audience),
        "policies": [{
            "environment": environment,
            "numeric_id_fingerprint": _fingerprint(principal.numeric_id),
            "application_id_fingerprint": _fingerprint(principal.application_id),
            "subject_fingerprint": _fingerprint(subject(config.repository, environment)),
        } for environment, principal in config.principals.items()],
        "read_back_verification_planned": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if not args.apply and not args.verify:
        print(json.dumps(dry_run_summary(config), sort_keys=True))
        return 0
    cli = DatabricksCli(config, timeout_seconds=args.timeout_seconds)
    result = verify_policies(config, cli=cli) if args.verify else ensure_policies(config, cli=cli)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
