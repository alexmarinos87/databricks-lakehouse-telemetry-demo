#!/usr/bin/env python3
"""Dry-run-first bootstrap for the distinct Databricks runtime identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ISSUER = "https://token.actions.githubusercontent.com"
ENVIRONMENTS = ("dev-plan", "prod-plan", "dev", "prod")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NUMERIC_ID = re.compile(r"[0-9]{1,32}\Z")


@dataclass(frozen=True)
class RuntimeEnvironment:
    deployment_client_id: str
    runtime_client_id: str
    runtime_numeric_id: str


@dataclass(frozen=True)
class RuntimeBootstrapConfig:
    repository: str
    account_host: str
    account_id: str
    audience: str
    environments: Mapping[str, RuntimeEnvironment]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_identifier(value: Any, *, label: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or any(ch.isspace() for ch in cleaned):
        raise ValueError(f"{label} is invalid")
    return cleaned


def load_config(path: str | Path) -> RuntimeBootstrapConfig:
    config_path = Path(path)
    try:
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("runtime identity config must be a regular file")
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("runtime identity config could not be parsed") from None

    expected = {"repository", "account_host", "account_id", "audience", "environments"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("runtime identity config has an invalid top-level shape")
    repository = payload["repository"]
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("repository must use owner/name form")
    account_host = _safe_identifier(
        payload["account_host"], label="account host", maximum=512
    ).rstrip("/")
    if not account_host.startswith("https://"):
        raise ValueError("account host must be an https URL")
    account_id = _safe_identifier(payload["account_id"], label="account ID")
    audience = _safe_identifier(payload["audience"], label="audience", maximum=512)

    raw_environments = payload["environments"]
    if not isinstance(raw_environments, dict) or set(raw_environments) != set(
        ENVIRONMENTS
    ):
        raise ValueError("runtime identity config must define four environments")
    environments: dict[str, RuntimeEnvironment] = {}
    for name in ENVIRONMENTS:
        raw = raw_environments[name]
        if not isinstance(raw, dict) or set(raw) != {
            "deployment_client_id",
            "runtime_client_id",
            "runtime_numeric_id",
        }:
            raise ValueError(f"environment {name} has an invalid shape")
        deployment_client_id = _safe_identifier(
            raw["deployment_client_id"], label=f"{name} deployment client ID"
        )
        runtime_client_id = _safe_identifier(
            raw["runtime_client_id"], label=f"{name} runtime client ID"
        )
        runtime_numeric_id = _safe_identifier(
            raw["runtime_numeric_id"], label=f"{name} runtime numeric ID"
        )
        if not _NUMERIC_ID.fullmatch(runtime_numeric_id):
            raise ValueError(f"{name} runtime numeric ID is invalid")
        if deployment_client_id == runtime_client_id:
            raise ValueError(
                f"{name} deployment and runtime client IDs must be distinct"
            )
        environments[name] = RuntimeEnvironment(
            deployment_client_id=deployment_client_id,
            runtime_client_id=runtime_client_id,
            runtime_numeric_id=runtime_numeric_id,
        )
    return RuntimeBootstrapConfig(
        repository=repository,
        account_host=account_host,
        account_id=account_id,
        audience=audience,
        environments=environments,
    )


def subject(repository: str, environment: str) -> str:
    if environment not in ENVIRONMENTS:
        raise ValueError("unsupported GitHub environment")
    return f"repo:{repository}:environment:{environment}"


class GitHubClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GITHUB_ADMIN_TOKEN is required for GitHub apply")
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        expected: tuple[int, ...] = (200, 201, 204),
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "lakehouse-demo-runtime-identity-bootstrap",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = int(response.status)
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"GitHub API {method} {path} failed with status {exc.code}"
            ) from None
        except (OSError, TimeoutError):
            raise RuntimeError(
                f"GitHub API {method} {path} could not be completed"
            ) from None
        if status not in expected:
            raise RuntimeError(
                f"GitHub API {method} {path} returned status {status}"
            )
        if not response_body:
            return None
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            raise RuntimeError("GitHub API returned invalid JSON") from None


class DatabricksCli:
    def __init__(self, config: RuntimeBootstrapConfig, timeout_seconds: float) -> None:
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
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=self._environment,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"Databricks CLI command exceeded {self._timeout_seconds:g} seconds"
            ) from None
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Databricks CLI command failed with exit code {exc.returncode}"
            ) from None
        except OSError:
            raise RuntimeError("Databricks CLI command could not be started") from None
        try:
            return json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            raise RuntimeError("Databricks CLI returned invalid JSON") from None


def _policy_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("policies", "items", "results"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def apply_github(config: RuntimeBootstrapConfig, client: GitHubClient) -> list[dict[str, str]]:
    repository = client.request("GET", f"/repos/{config.repository}")
    if not isinstance(repository, dict) or not isinstance(repository.get("id"), int):
        raise RuntimeError("GitHub repository metadata did not include an integer ID")
    repository_id = int(repository["id"])
    results: list[dict[str, str]] = []
    for environment, identity in config.environments.items():
        base = f"/repositories/{repository_id}/environments/{environment}/variables"
        existing = client.request("GET", base)
        names = {
            str(item.get("name"))
            for item in existing.get("variables", [])
            if isinstance(existing, dict) and isinstance(item, dict)
        }
        payload = {
            "name": "DATABRICKS_RUNTIME_CLIENT_ID",
            "value": identity.runtime_client_id,
        }
        if "DATABRICKS_RUNTIME_CLIENT_ID" in names:
            client.request(
                "PATCH",
                f"{base}/DATABRICKS_RUNTIME_CLIENT_ID",
                payload,
            )
        else:
            client.request("POST", base, payload, expected=(201,))
        results.append(
            {
                "environment": environment,
                "runtime_client_id_fingerprint": _fingerprint(
                    identity.runtime_client_id
                ),
            }
        )
    return results


def apply_databricks(
    config: RuntimeBootstrapConfig, cli: DatabricksCli
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for environment, identity in config.environments.items():
        expected_subject = subject(config.repository, environment)
        listed = cli.json(
            [
                "databricks",
                "account",
                "service-principal-federation-policy",
                "list",
                identity.runtime_numeric_id,
                "-o",
                "json",
            ]
        )
        matches = [
            item
            for item in _policy_items(listed)
            if isinstance(item.get("oidc_policy"), dict)
            and item["oidc_policy"].get("subject") == expected_subject
        ]
        if len(matches) > 1:
            raise RuntimeError("multiple runtime federation policies share one subject")
        expected_policy = {
            "issuer": ISSUER,
            "audiences": [config.audience],
            "subject": expected_subject,
        }
        if matches:
            actual = matches[0]["oidc_policy"]
            if (
                actual.get("issuer") != expected_policy["issuer"]
                or actual.get("audiences") != expected_policy["audiences"]
            ):
                raise RuntimeError(
                    "existing runtime federation policy conflicts with requested policy"
                )
            outcome = "verified"
        else:
            cli.json(
                [
                    "databricks",
                    "account",
                    "service-principal-federation-policy",
                    "create",
                    identity.runtime_numeric_id,
                    "--json",
                    json.dumps({"oidc_policy": expected_policy}, sort_keys=True),
                    "-o",
                    "json",
                ]
            )
            outcome = "created"
        results.append(
            {
                "environment": environment,
                "outcome": outcome,
                "subject_fingerprint": _fingerprint(expected_subject),
                "runtime_client_id_fingerprint": _fingerprint(
                    identity.runtime_client_id
                ),
            }
        )
    return results


def dry_run(config: RuntimeBootstrapConfig) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "repository": config.repository,
        "account_id_fingerprint": _fingerprint(config.account_id),
        "audience_fingerprint": _fingerprint(config.audience),
        "environments": [
            {
                "environment": name,
                "deployment_client_id_fingerprint": _fingerprint(
                    identity.deployment_client_id
                ),
                "runtime_client_id_fingerprint": _fingerprint(
                    identity.runtime_client_id
                ),
                "runtime_numeric_id_fingerprint": _fingerprint(
                    identity.runtime_numeric_id
                ),
                "subject": subject(config.repository, name),
            }
            for name, identity in config.environments.items()
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply-github", action="store_true")
    parser.add_argument("--apply-databricks", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if not args.apply_github and not args.apply_databricks:
        print(json.dumps(dry_run(config), sort_keys=True))
        return 0
    result: dict[str, Any] = {"repository": config.repository}
    if args.apply_github:
        result["github"] = apply_github(
            config, GitHubClient(os.environ.get("GITHUB_ADMIN_TOKEN", ""))
        )
    if args.apply_databricks:
        result["databricks"] = apply_databricks(
            config, DatabricksCli(config, args.timeout_seconds)
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
