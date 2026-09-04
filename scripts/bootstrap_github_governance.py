#!/usr/bin/env python3
"""Dry-run-first bootstrap for repository settings and GitHub environments."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_BRANCH = "main"
REQUIRED_ENVIRONMENTS = ("dev-plan", "prod-plan", "dev", "prod")
REQUIRED_STATUS_CONTEXTS = (
    "validate",
    "Round-trip synthetic review evidence",
)
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class GitHubApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnvironmentValues:
    databricks_host: str
    databricks_client_id: str


@dataclass(frozen=True)
class BootstrapConfig:
    repository: str
    environments: Mapping[str, EnvironmentValues]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _validate_host(value: str) -> str:
    host = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(host)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Databricks host must be an https URL")
    if len(host) > 512:
        raise ValueError("Databricks host is too long")
    return host


def _validate_client_id(value: str) -> str:
    client_id = value.strip()
    if not client_id or len(client_id) > 256 or any(ch.isspace() for ch in client_id):
        raise ValueError("Databricks client ID is invalid")
    return client_id


def load_config(path: str | Path) -> BootstrapConfig:
    config_path = Path(path)
    try:
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("bootstrap config must be a regular file")
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("bootstrap config could not be parsed") from None
    if not isinstance(payload, dict) or set(payload) != {"repository", "environments"}:
        raise ValueError("bootstrap config has an invalid top-level shape")
    repository = payload["repository"]
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("repository must use owner/name form")
    raw_environments = payload["environments"]
    if not isinstance(raw_environments, dict) or set(raw_environments) != set(REQUIRED_ENVIRONMENTS):
        raise ValueError("bootstrap config must define the four required environments")
    environments: dict[str, EnvironmentValues] = {}
    for name in REQUIRED_ENVIRONMENTS:
        raw = raw_environments[name]
        if not isinstance(raw, dict) or set(raw) != {"databricks_host", "databricks_client_id"}:
            raise ValueError(f"environment {name} has an invalid shape")
        if not all(isinstance(raw[key], str) for key in raw):
            raise ValueError(f"environment {name} values must be strings")
        environments[name] = EnvironmentValues(
            databricks_host=_validate_host(raw["databricks_host"]),
            databricks_client_id=_validate_client_id(raw["databricks_client_id"]),
        )
    return BootstrapConfig(repository=repository, environments=environments)


def repository_settings_payload() -> dict[str, Any]:
    return {
        "allow_squash_merge": True,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
        "use_squash_pr_title_as_default": True,
        "squash_merge_commit_title": "PR_TITLE",
        "squash_merge_commit_message": "PR_BODY",
    }


def branch_protection_payload(*, required_approvals: int = 0) -> dict[str, Any]:
    if required_approvals not in (0, 1):
        raise ValueError("required approvals must be zero or one")
    return {
        "required_status_checks": {"strict": True, "contexts": list(REQUIRED_STATUS_CONTEXTS)},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": required_approvals,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": True,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def environment_payload() -> dict[str, Any]:
    return {
        "wait_timer": 0,
        "prevent_self_review": False,
        "reviewers": [],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }


class GitHubClient:
    def __init__(self, token: str, *, api_url: str = "https://api.github.com") -> None:
        if not token:
            raise ValueError("GITHUB_ADMIN_TOKEN is required for --apply")
        self._token = token
        self._api_url = api_url.rstrip("/")

    def request(self, method: str, path: str, payload: Mapping[str, Any] | None = None,
                *, acceptable_statuses: tuple[int, ...] = (200, 201, 204)) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self._api_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "lakehouse-demo-governance-bootstrap",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, response_body = int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in acceptable_statuses:
                return None
            raise GitHubApiError(f"GitHub API {method} {path} failed with status {exc.code}") from None
        except (OSError, TimeoutError):
            raise GitHubApiError(f"GitHub API {method} {path} could not be completed") from None
        if status not in acceptable_statuses:
            raise GitHubApiError(f"GitHub API {method} {path} returned status {status}")
        if not response_body:
            return None
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            raise GitHubApiError(f"GitHub API {method} {path} returned invalid JSON") from None


def _existing_names(payload: Any, key: str) -> set[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        return set()
    return {str(item["name"]) for item in payload[key] if isinstance(item, dict) and item.get("name")}


def _ensure_environment_variable(client: GitHubClient, *, repository_id: int,
                                 environment: str, existing_names: set[str],
                                 name: str, value: str) -> None:
    base = f"/repositories/{repository_id}/environments/{environment}/variables"
    if name in existing_names:
        client.request("PATCH", f"{base}/{name}", {"name": name, "value": value})
    else:
        client.request("POST", base, {"name": name, "value": value}, acceptable_statuses=(201,))


def apply_governance(config: BootstrapConfig, *, client: GitHubClient,
                     required_approvals: int = 0) -> dict[str, Any]:
    repository_path = f"/repos/{config.repository}"
    repository = client.request("GET", repository_path)
    if not isinstance(repository, dict) or not isinstance(repository.get("id"), int):
        raise GitHubApiError("GitHub repository metadata did not include an integer ID")
    repository_id = int(repository["id"])
    client.request("PATCH", repository_path, repository_settings_payload())
    client.request("PUT", f"{repository_path}/branches/{DEFAULT_BRANCH}/protection",
                   branch_protection_payload(required_approvals=required_approvals))
    summaries = []
    for environment, values in config.environments.items():
        environment_path = f"{repository_path}/environments/{environment}"
        client.request("PUT", environment_path, environment_payload())
        policies = client.request("GET", f"{environment_path}/deployment-branch-policies")
        if DEFAULT_BRANCH not in _existing_names(policies, "branch_policies"):
            client.request("POST", f"{environment_path}/deployment-branch-policies",
                           {"name": DEFAULT_BRANCH, "type": "branch"},
                           acceptable_statuses=(200, 201))
        variables = client.request("GET", f"/repositories/{repository_id}/environments/{environment}/variables")
        existing = _existing_names(variables, "variables")
        _ensure_environment_variable(client, repository_id=repository_id, environment=environment,
                                     existing_names=existing, name="DATABRICKS_HOST",
                                     value=values.databricks_host)
        _ensure_environment_variable(client, repository_id=repository_id, environment=environment,
                                     existing_names=existing, name="DATABRICKS_CLIENT_ID",
                                     value=values.databricks_client_id)
        summaries.append({
            "environment": environment,
            "host_fingerprint": _fingerprint(values.databricks_host),
            "client_id_fingerprint": _fingerprint(values.databricks_client_id),
        })
    branch = client.request("GET", f"{repository_path}/branches/{DEFAULT_BRANCH}")
    if not isinstance(branch, dict) or branch.get("protected") is not True:
        raise GitHubApiError("main protection did not become active")
    settings = client.request("GET", repository_path)
    if not isinstance(settings, dict) or settings.get("allow_merge_commit") or settings.get("allow_rebase_merge"):
        raise GitHubApiError("non-squash merge methods remain enabled")
    return {
        "repository": config.repository,
        "branch": DEFAULT_BRANCH,
        "protected": True,
        "required_status_contexts": list(REQUIRED_STATUS_CONTEXTS),
        "required_approvals": required_approvals,
        "environments": summaries,
    }


def dry_run_summary(config: BootstrapConfig, *, required_approvals: int) -> dict[str, Any]:
    branch_protection_payload(required_approvals=required_approvals)
    return {
        "mode": "dry-run",
        "repository": config.repository,
        "branch": DEFAULT_BRANCH,
        "required_status_contexts": list(REQUIRED_STATUS_CONTEXTS),
        "required_approvals": required_approvals,
        "environments": [{
            "environment": name,
            "host_fingerprint": _fingerprint(values.databricks_host),
            "client_id_fingerprint": _fingerprint(values.databricks_client_id),
        } for name, values in config.environments.items()],
        "writes_planned": 2 + 5 * len(config.environments),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--required-approvals", type=int, choices=(0, 1), default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if not args.apply:
        print(json.dumps(dry_run_summary(config, required_approvals=args.required_approvals), sort_keys=True))
        return 0
    result = apply_governance(config, client=GitHubClient(os.environ.get("GITHUB_ADMIN_TOKEN", "")),
                              required_approvals=args.required_approvals)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
