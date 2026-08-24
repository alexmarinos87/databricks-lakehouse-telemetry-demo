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
REQUIRED_STATUS_CONTEXT = "validate"
REQUIRED_ENVIRONMENT_VARIABLES = ("DATABRICKS_HOST", "DATABRICKS_CLIENT_ID")
FORBIDDEN_STATIC_CREDENTIAL = "DATABRICKS_CLIENT_SECRET"
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class GitHubApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reviewer:
    type: str
    id: int


@dataclass(frozen=True)
class EnvironmentValues:
    databricks_host: str
    databricks_client_id: str
    reviewers: tuple[Reviewer, ...]
    prevent_self_review: bool


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
        expected = {"databricks_host", "databricks_client_id", "reviewers", "prevent_self_review"}
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError(f"environment {name} has an invalid shape")
        if not isinstance(raw["databricks_host"], str) or not isinstance(raw["databricks_client_id"], str):
            raise ValueError(f"environment {name} Databricks values must be strings")
        raw_reviewers = raw["reviewers"]
        if not isinstance(raw_reviewers, list) or len(raw_reviewers) > 6:
            raise ValueError(f"environment {name} reviewers must be a list of at most six")
        reviewers: list[Reviewer] = []
        seen: set[tuple[str, int]] = set()
        for raw_reviewer in raw_reviewers:
            if (not isinstance(raw_reviewer, dict)
                    or set(raw_reviewer) != {"type", "id"}
                    or raw_reviewer.get("type") not in {"User", "Team"}
                    or not isinstance(raw_reviewer.get("id"), int)
                    or raw_reviewer["id"] <= 0):
                raise ValueError(f"environment {name} reviewer is invalid")
            key = (str(raw_reviewer["type"]), int(raw_reviewer["id"]))
            if key in seen:
                raise ValueError(f"environment {name} contains a duplicate reviewer")
            seen.add(key)
            reviewers.append(Reviewer(*key))
        prevent_self_review = raw["prevent_self_review"]
        if not isinstance(prevent_self_review, bool):
            raise ValueError(f"environment {name} prevent_self_review must be boolean")
        if name in {"dev-plan", "prod-plan"} and (reviewers or prevent_self_review):
            raise ValueError(f"environment {name} must remain plan-only without reviewers")
        if name == "prod" and (not reviewers or not prevent_self_review):
            raise ValueError("environment prod requires a reviewer and self-review prevention")
        if name == "dev" and reviewers and not prevent_self_review:
            raise ValueError("environment dev reviewers require self-review prevention")
        if not reviewers and prevent_self_review:
            raise ValueError(f"environment {name} cannot prevent self-review without reviewers")
        environments[name] = EnvironmentValues(
            databricks_host=_validate_host(raw["databricks_host"]),
            databricks_client_id=_validate_client_id(raw["databricks_client_id"]),
            reviewers=tuple(reviewers),
            prevent_self_review=prevent_self_review,
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
        "required_status_checks": {"strict": True, "contexts": [REQUIRED_STATUS_CONTEXT]},
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


def environment_payload(values: EnvironmentValues) -> dict[str, Any]:
    return {
        "wait_timer": 0,
        "prevent_self_review": values.prevent_self_review,
        "reviewers": [{"type": reviewer.type, "id": reviewer.id} for reviewer in values.reviewers],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }


class GitHubClient:
    def __init__(self, token: str, *, api_url: str = "https://api.github.com") -> None:
        if not token:
            raise ValueError("GITHUB_ADMIN_TOKEN is required for GitHub apply or verification")
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


def _named_items(payload: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise GitHubApiError(f"{label} did not include a {key} collection")
    result: dict[str, dict[str, Any]] = {}
    for item in payload[key]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise GitHubApiError(f"{label} included a malformed item")
        if item["name"] in result:
            raise GitHubApiError(f"{label} included duplicate name {item['name']}")
        result[item["name"]] = item
    return result


def _enabled(value: Any) -> bool:
    return value is True or (isinstance(value, dict) and value.get("enabled") is True)


def _status_contexts(payload: Any) -> set[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("contexts"), list):
        raise GitHubApiError("main protection status checks are malformed")
    result = {str(context) for context in payload["contexts"]}
    checks = payload.get("checks", [])
    if not isinstance(checks, list):
        raise GitHubApiError("main protection status checks are malformed")
    for check in checks:
        if not isinstance(check, dict):
            raise GitHubApiError("main protection included a malformed status check")
        if isinstance(check.get("context"), str):
            result.add(check["context"])
    return result


def _actual_reviewers(payload: Mapping[str, Any], environment: str) -> tuple[set[tuple[str, int]], bool]:
    rules = payload.get("protection_rules", [])
    if not isinstance(rules, list):
        raise GitHubApiError(f"environment {environment} protection rules are malformed")
    reviewer_rules = [rule for rule in rules
                      if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]
    if len(reviewer_rules) > 1:
        raise GitHubApiError(f"environment {environment} has duplicate reviewer rules")
    if not reviewer_rules:
        return set(), False
    rule = reviewer_rules[0]
    if not isinstance(rule.get("reviewers"), list) or not isinstance(rule.get("prevent_self_review"), bool):
        raise GitHubApiError(f"environment {environment} reviewer rule is malformed")
    reviewers: set[tuple[str, int]] = set()
    for item in rule["reviewers"]:
        if not isinstance(item, dict):
            raise GitHubApiError(f"environment {environment} reviewer is malformed")
        nested = item.get("reviewer")
        reviewer = nested if isinstance(nested, dict) else item
        key = (item.get("type"), reviewer.get("id"))
        if key[0] not in {"User", "Team"} or not isinstance(key[1], int) or key[1] <= 0:
            raise GitHubApiError(f"environment {environment} reviewer is malformed")
        normalized = (str(key[0]), int(key[1]))
        if normalized in reviewers:
            raise GitHubApiError(f"environment {environment} has duplicate reviewers")
        reviewers.add(normalized)
    return reviewers, bool(rule["prevent_self_review"])


def _verify_repository(settings: Any) -> int:
    if not isinstance(settings, dict) or not isinstance(settings.get("id"), int):
        raise GitHubApiError("GitHub repository metadata did not include an integer ID")
    expected = {
        "allow_squash_merge": True,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    for key, value in expected.items():
        if settings.get(key) is not value:
            raise GitHubApiError(f"repository setting {key} does not match policy")
    return int(settings["id"])


def _verify_protection(payload: Any, required_approvals: int) -> None:
    if not isinstance(payload, dict):
        raise GitHubApiError("main protection response is malformed")
    status = payload.get("required_status_checks")
    if not isinstance(status, dict) or status.get("strict") is not True:
        raise GitHubApiError("main protection does not require a current branch")
    if REQUIRED_STATUS_CONTEXT not in _status_contexts(status):
        raise GitHubApiError(f"main protection does not require {REQUIRED_STATUS_CONTEXT}")
    if not _enabled(payload.get("enforce_admins")):
        raise GitHubApiError("main protection does not enforce administrators")
    reviews = payload.get("required_pull_request_reviews")
    if (not isinstance(reviews, dict)
            or reviews.get("dismiss_stale_reviews") is not True
            or reviews.get("required_approving_review_count") != required_approvals):
        raise GitHubApiError("main protection review policy does not match")
    if not _enabled(payload.get("required_linear_history")):
        raise GitHubApiError("main protection does not require linear history")
    if _enabled(payload.get("allow_force_pushes")) or _enabled(payload.get("allow_deletions")):
        raise GitHubApiError("main protection permits force push or deletion")
    if not _enabled(payload.get("required_conversation_resolution")):
        raise GitHubApiError("main protection does not require conversation resolution")


def _verify_environment(config: BootstrapConfig, client: GitHubClient,
                        repository_id: int, environment: str) -> dict[str, Any]:
    repository_path = f"/repos/{config.repository}"
    environment_path = f"{repository_path}/environments/{environment}"
    state = client.request("GET", environment_path)
    if not isinstance(state, dict):
        raise GitHubApiError(f"environment {environment} response is malformed")
    branch_policy = state.get("deployment_branch_policy")
    if (not isinstance(branch_policy, dict)
            or branch_policy.get("protected_branches") is not False
            or branch_policy.get("custom_branch_policies") is not True):
        raise GitHubApiError(f"environment {environment} branch policy does not match")
    policies = _named_items(
        client.request("GET", f"{environment_path}/deployment-branch-policies"),
        "branch_policies", f"environment {environment} branch policies")
    if set(policies) != {DEFAULT_BRANCH}:
        raise GitHubApiError(f"environment {environment} does not allow only the main branch")
    values = config.environments[environment]
    expected_reviewers = {(reviewer.type, reviewer.id) for reviewer in values.reviewers}
    reviewers, prevent_self_review = _actual_reviewers(state, environment)
    if reviewers != expected_reviewers or prevent_self_review is not values.prevent_self_review:
        raise GitHubApiError(f"environment {environment} reviewer policy does not match config")
    variables = _named_items(
        client.request("GET", f"/repositories/{repository_id}/environments/{environment}/variables"),
        "variables", f"environment {environment} variables")
    if FORBIDDEN_STATIC_CREDENTIAL in variables:
        raise GitHubApiError(f"environment {environment} contains the forbidden static credential")
    expected_values = {
        "DATABRICKS_HOST": values.databricks_host,
        "DATABRICKS_CLIENT_ID": values.databricks_client_id,
    }
    verified_variables = []
    for name in REQUIRED_ENVIRONMENT_VARIABLES:
        actual = variables.get(name, {}).get("value")
        if not isinstance(actual, str) or actual != expected_values[name]:
            raise GitHubApiError(f"environment {environment} variable {name} does not match config")
        verified_variables.append({"name": name, "value_fingerprint": _fingerprint(actual)})
    secrets = _named_items(
        client.request("GET", f"/repositories/{repository_id}/environments/{environment}/secrets"),
        "secrets", f"environment {environment} secrets")
    if FORBIDDEN_STATIC_CREDENTIAL in secrets:
        raise GitHubApiError(f"environment {environment} contains the forbidden static secret")
    return {
        "environment": environment,
        "reviewer_fingerprints": [
            {"type": kind, "id_fingerprint": _fingerprint(str(identifier))}
            for kind, identifier in sorted(reviewers)
        ],
        "prevent_self_review": prevent_self_review,
        "variables": verified_variables,
        "forbidden_static_credential_absent": True,
    }


def verify_governance(config: BootstrapConfig, *, client: GitHubClient,
                      required_approvals: int = 0) -> dict[str, Any]:
    branch_protection_payload(required_approvals=required_approvals)
    repository_path = f"/repos/{config.repository}"
    repository_id = _verify_repository(client.request("GET", repository_path))
    branch = client.request("GET", f"{repository_path}/branches/{DEFAULT_BRANCH}")
    if not isinstance(branch, dict) or branch.get("protected") is not True:
        raise GitHubApiError("main protection is not active")
    _verify_protection(
        client.request("GET", f"{repository_path}/branches/{DEFAULT_BRANCH}/protection"),
        required_approvals)
    environments = [
        _verify_environment(config, client, repository_id, environment)
        for environment in REQUIRED_ENVIRONMENTS
    ]
    return {
        "mode": "verify",
        "repository": config.repository,
        "branch": DEFAULT_BRANCH,
        "required_status_context": REQUIRED_STATUS_CONTEXT,
        "required_approvals": required_approvals,
        "environments": environments,
        "write_operations": 0,
    }


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
    for environment, values in config.environments.items():
        environment_path = f"{repository_path}/environments/{environment}"
        client.request("PUT", environment_path, environment_payload(values))
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
    verified = verify_governance(
        config, client=client, required_approvals=required_approvals)
    return {**verified, "mode": "apply", "read_back_verified": True}


def dry_run_summary(config: BootstrapConfig, *, required_approvals: int) -> dict[str, Any]:
    branch_protection_payload(required_approvals=required_approvals)
    return {
        "mode": "dry-run",
        "repository": config.repository,
        "branch": DEFAULT_BRANCH,
        "required_status_context": REQUIRED_STATUS_CONTEXT,
        "required_approvals": required_approvals,
        "environments": [{
            "environment": name,
            "host_fingerprint": _fingerprint(values.databricks_host),
            "client_id_fingerprint": _fingerprint(values.databricks_client_id),
            "reviewer_fingerprints": [
                {"type": reviewer.type, "id_fingerprint": _fingerprint(str(reviewer.id))}
                for reviewer in values.reviewers
            ],
            "prevent_self_review": values.prevent_self_review,
        } for name, values in config.environments.items()],
        "writes_planned": 2 + 5 * len(config.environments),
        "read_back_verification_planned": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--required-approvals", type=int, choices=(0, 1), default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if not args.apply and not args.verify:
        print(json.dumps(dry_run_summary(config, required_approvals=args.required_approvals), sort_keys=True))
        return 0
    client = GitHubClient(os.environ.get("GITHUB_ADMIN_TOKEN", ""))
    if args.verify:
        result = verify_governance(config, client=client, required_approvals=args.required_approvals)
    else:
        result = apply_governance(config, client=client, required_approvals=args.required_approvals)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
