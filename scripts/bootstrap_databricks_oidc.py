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
    for environment in REQUIRED_ENVIRONMENTS:
        raw = raw_principals[environment]
        if not isinstance(raw, dict) or set(raw) != {"numeric_id", "application_id"}:
            raise ValueError(f"principal {environment} has an invalid shape")
        numeric_id = raw["numeric_id"]
        application_id = raw["application_id"]
        if not isinstance(numeric_id, str) or not _NUMERIC_ID_PATTERN.fullmatch(numeric_id):
            raise ValueError(f"principal {environment} numeric ID is invalid")
        if not isinstance(application_id, str) or not application_id.strip():
            raise ValueError(f"principal {environment} application ID is invalid")
        principals[environment] = PrincipalConfig(numeric_id, application_id.strip())
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
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("policies", "items", "results"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def _matching_subject(policies: list[dict[str, Any]], expected_subject: str) -> list[dict[str, Any]]:
    return [policy for policy in policies
            if isinstance(policy.get("oidc_policy"), dict)
            and policy["oidc_policy"].get("subject") == expected_subject]


def ensure_policies(config: FederationConfig, *, cli: DatabricksCli) -> dict[str, Any]:
    results = []
    for environment, principal in config.principals.items():
        listed = cli.json([
            "databricks", "account", "service-principal-federation-policy", "list",
            principal.numeric_id, "-o", "json",
        ])
        matches = _matching_subject(_policies(listed), subject(config.repository, environment))
        if len(matches) > 1:
            raise RuntimeError("multiple Databricks federation policies share one subject")
        expected = policy_payload(config, environment)["oidc_policy"]
        if matches:
            actual = matches[0].get("oidc_policy", {})
            if actual.get("issuer") != expected["issuer"] or actual.get("audiences") != expected["audiences"]:
                raise RuntimeError("existing Databricks federation policy conflicts with the requested policy")
            outcome = "verified"
        else:
            cli.json([
                "databricks", "account", "service-principal-federation-policy", "create",
                principal.numeric_id, "--json",
                json.dumps(policy_payload(config, environment), sort_keys=True), "-o", "json",
            ])
            outcome = "created"
        results.append({
            "environment": environment,
            "outcome": outcome,
            "application_id_fingerprint": _fingerprint(principal.application_id),
            "subject_fingerprint": _fingerprint(subject(config.repository, environment)),
        })
    return {
        "repository": config.repository,
        "issuer": ISSUER,
        "audience_fingerprint": _fingerprint(config.audience),
        "policies": results,
    }


def dry_run_summary(config: FederationConfig) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "repository": config.repository,
        "account_host": config.account_host,
        "account_id_fingerprint": _fingerprint(config.account_id),
        "audience_fingerprint": _fingerprint(config.audience),
        "policies": [{
            "environment": environment,
            "numeric_id_fingerprint": _fingerprint(principal.numeric_id),
            "application_id_fingerprint": _fingerprint(principal.application_id),
            "subject": subject(config.repository, environment),
        } for environment, principal in config.principals.items()],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if not args.apply:
        print(json.dumps(dry_run_summary(config), sort_keys=True))
        return 0
    result = ensure_policies(config, cli=DatabricksCli(config, timeout_seconds=args.timeout_seconds))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
