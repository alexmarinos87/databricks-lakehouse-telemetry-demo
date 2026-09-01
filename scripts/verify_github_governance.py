#!/usr/bin/env python3
"""Verify effective GitHub governance without mutating repository settings.

The verifier reads the ignored bootstrap configuration, queries repository and
environment state with an administrative read token, and writes sanitized JSON
and Markdown evidence. It never writes to GitHub and never contacts Databricks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


DEFAULT_API_URL = "https://api.github.com"
DEFAULT_BRANCH = "main"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_PAGES = 10
PAGE_SIZE = 100
MAX_FINDINGS = 100
REQUIRED_ENVIRONMENTS = ("dev-plan", "prod-plan", "dev", "prod")
REQUIRED_STATUS_CONTEXTS = (
    "validate",
    "Round-trip synthetic review evidence",
)
FORBIDDEN_STATIC_SECRET = "DATABRICKS_CLIENT_SECRET"
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NUMERIC_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class VerificationError(RuntimeError):
    """A bounded failure that is safe to persist without provider diagnostics."""

    def __init__(self, stage: str, category: str) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage = stage
        self.category = category


@dataclass(frozen=True)
class ExpectedEnvironment:
    databricks_host: str
    deployment_client_id: str
    runtime_client_id: str


@dataclass(frozen=True)
class VerificationConfig:
    repository: str
    environments: Mapping[str, ExpectedEnvironment]


class ReadOnlyGitHub(Protocol):
    def get(self, path: str) -> Any: ...

    def list_all(self, path: str, key: str) -> list[dict[str, Any]]: ...


def positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(
            "must be a finite positive number of seconds"
        )
    return parsed


def _safe_identifier(value: Any, *, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned.encode("utf-8")) > maximum
        or any(character.isspace() for character in cleaned)
        or any(character in cleaned for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError(f"{label} is invalid")
    return cleaned


def _validate_host(value: Any, *, label: str) -> str:
    host = _safe_identifier(value, label=label).rstrip("/")
    parsed = urllib.parse.urlparse(host)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an https workspace URL")
    return host


def _load_json_file(path: str | Path, *, label: str) -> Mapping[str, Any]:
    config_path = Path(path)
    try:
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError(f"{label} must be a regular file")
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{label} could not be parsed") from None
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def load_config(
    github_config_path: str | Path,
    runtime_config_path: str | Path,
) -> VerificationConfig:
    """Load independent expected values from both ignored bootstrap files."""

    github_payload = _load_json_file(
        github_config_path, label="GitHub governance config"
    )
    if set(github_payload) != {"repository", "environments"}:
        raise ValueError("GitHub governance config has an invalid top-level shape")
    repository = github_payload["repository"]
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise ValueError("repository must use owner/name form")

    raw_github_environments = github_payload["environments"]
    if not isinstance(raw_github_environments, dict) or set(
        raw_github_environments
    ) != set(REQUIRED_ENVIRONMENTS):
        raise ValueError("GitHub governance config must define four environments")

    runtime_payload = _load_json_file(
        runtime_config_path, label="runtime identity config"
    )
    expected_runtime_keys = {
        "repository",
        "account_host",
        "account_id",
        "audience",
        "environments",
    }
    if set(runtime_payload) != expected_runtime_keys:
        raise ValueError("runtime identity config has an invalid top-level shape")
    if runtime_payload["repository"] != repository:
        raise ValueError("bootstrap configs refer to different repositories")
    _safe_identifier(runtime_payload["account_host"], label="account host")
    _safe_identifier(runtime_payload["account_id"], label="account ID")
    _safe_identifier(runtime_payload["audience"], label="audience")

    raw_runtime_environments = runtime_payload["environments"]
    if not isinstance(raw_runtime_environments, dict) or set(
        raw_runtime_environments
    ) != set(REQUIRED_ENVIRONMENTS):
        raise ValueError("runtime identity config must define four environments")

    environments: dict[str, ExpectedEnvironment] = {}
    for environment in REQUIRED_ENVIRONMENTS:
        github_environment = raw_github_environments[environment]
        if not isinstance(github_environment, dict) or set(github_environment) != {
            "databricks_host",
            "databricks_client_id",
        }:
            raise ValueError(f"GitHub environment {environment} has an invalid shape")
        runtime_environment = raw_runtime_environments[environment]
        if not isinstance(runtime_environment, dict) or set(runtime_environment) != {
            "deployment_client_id",
            "runtime_client_id",
            "runtime_numeric_id",
        }:
            raise ValueError(f"runtime environment {environment} has an invalid shape")

        host = _validate_host(
            github_environment["databricks_host"],
            label=f"{environment} Databricks host",
        )
        deployment_client_id = _safe_identifier(
            github_environment["databricks_client_id"],
            label=f"{environment} deployment client ID",
            maximum=256,
        )
        runtime_deployment_client_id = _safe_identifier(
            runtime_environment["deployment_client_id"],
            label=f"{environment} runtime-config deployment client ID",
            maximum=256,
        )
        runtime_client_id = _safe_identifier(
            runtime_environment["runtime_client_id"],
            label=f"{environment} runtime client ID",
            maximum=256,
        )
        runtime_numeric_id = _safe_identifier(
            runtime_environment["runtime_numeric_id"],
            label=f"{environment} runtime numeric ID",
            maximum=32,
        )
        if not _NUMERIC_ID_PATTERN.fullmatch(runtime_numeric_id):
            raise ValueError(f"{environment} runtime numeric ID is invalid")
        if deployment_client_id != runtime_deployment_client_id:
            raise ValueError(
                f"{environment} deployment client ID differs between bootstrap configs"
            )
        if deployment_client_id == runtime_client_id:
            raise ValueError(
                f"{environment} deployment and runtime client IDs must be distinct"
            )
        environments[environment] = ExpectedEnvironment(
            databricks_host=host,
            deployment_client_id=deployment_client_id,
            runtime_client_id=runtime_client_id,
        )

    return VerificationConfig(repository=repository, environments=environments)


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise VerificationError("configuration", "output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise VerificationError(
            "configuration", "output_directory_could_not_be_created"
        ) from None
    if path.is_symlink() or not path.is_dir():
        raise VerificationError(
            "configuration", "output_directory_is_not_regular"
        )
    return path


class GitHubClient:
    """Small read-only GitHub REST client with bounded pagination."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not token.strip():
            raise ValueError("GITHUB_ADMIN_TOKEN is required for verification")
        if api_url.strip().rstrip("/") != DEFAULT_API_URL:
            raise ValueError("GitHub API URL is not allowed")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout must be finite and positive")
        self._token = token.strip()
        self._api_url = DEFAULT_API_URL
        self._timeout_seconds = timeout_seconds

    def get(self, path: str) -> Any:
        if not path.startswith("/") or "\n" in path or "\r" in path:
            raise VerificationError("github", "request_path_is_invalid")
        request = urllib.request.Request(
            self._api_url + path,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "lakehouse-demo-governance-verifier",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                status = int(response.status)
                response_body = response.read()
        except urllib.error.HTTPError:
            raise VerificationError("github", "request_failed") from None
        except (OSError, TimeoutError, urllib.error.URLError):
            raise VerificationError("github", "request_failed") from None
        if status != 200:
            raise VerificationError("github", "request_failed")
        try:
            return json.loads(response_body or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise VerificationError("github", "invalid_json_response") from None

    def list_all(self, path: str, key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, MAX_PAGES + 1):
            payload = self.get(
                f"{path}{separator}per_page={PAGE_SIZE}&page={page}"
            )
            if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
                raise VerificationError("github", "inventory_response_shape_is_invalid")
            page_items = [
                item for item in payload[key] if isinstance(item, dict)
            ]
            items.extend(page_items)
            if len(page_items) < PAGE_SIZE:
                total_count = payload.get("total_count")
                if isinstance(total_count, int) and total_count > len(items):
                    raise VerificationError("github", "inventory_is_truncated")
                return items
        raise VerificationError("github", "inventory_exceeded_page_limit")


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, dict) and value.get("enabled") is True


def _status_contexts(required: Any) -> set[str]:
    if not isinstance(required, dict):
        return set()
    contexts = {
        item.strip()
        for item in required.get("contexts", [])
        if isinstance(item, str) and item.strip()
    }
    for check in required.get("checks", []):
        if not isinstance(check, dict):
            continue
        context = check.get("context")
        if isinstance(context, str) and context.strip():
            contexts.add(context.strip())
    return contexts


def _add_finding(
    findings: list[dict[str, str]], category: str, scope: str
) -> None:
    if len(findings) >= MAX_FINDINGS:
        if not any(item["category"] == "findings_truncated" for item in findings):
            findings.append({"category": "findings_truncated", "scope": "global"})
        return
    findings.append({"category": category, "scope": scope})


def verify_state(
    config: VerificationConfig,
    *,
    client: ReadOnlyGitHub,
    required_approvals: int,
) -> dict[str, Any]:
    """Compare effective GitHub state with the independently parsed expectation."""

    if required_approvals not in (0, 1):
        raise ValueError("required approvals must be zero or one")

    findings: list[dict[str, str]] = []
    repository_path = f"/repos/{config.repository}"
    repository = client.get(repository_path)
    if not isinstance(repository, dict) or not isinstance(repository.get("id"), int):
        raise VerificationError("github", "repository_response_shape_is_invalid")
    repository_id = int(repository["id"])

    expected_repository_settings = {
        "allow_squash_merge": True,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
        "use_squash_pr_title_as_default": True,
        "squash_merge_commit_title": "PR_TITLE",
        "squash_merge_commit_message": "PR_BODY",
    }
    repository_setting_results: dict[str, bool] = {}
    for name, expected in expected_repository_settings.items():
        matches = repository.get(name) == expected
        repository_setting_results[name] = matches
        if not matches:
            _add_finding(findings, "repository_setting_drift", name)

    branch_state = client.get(f"{repository_path}/branches/{DEFAULT_BRANCH}")
    if not isinstance(branch_state, dict):
        raise VerificationError("github", "branch_response_shape_is_invalid")
    branch_commit = branch_state.get("commit")
    branch_head_sha = branch_commit.get("sha") if isinstance(branch_commit, dict) else None
    if not isinstance(branch_head_sha, str) or not _SHA_PATTERN.fullmatch(branch_head_sha):
        raise VerificationError("github", "branch_head_sha_is_missing")
    main_protected = branch_state.get("protected") is True
    if not main_protected:
        _add_finding(findings, "main_branch_is_unprotected", DEFAULT_BRANCH)

    if main_protected:
        protection = client.get(
            f"{repository_path}/branches/{DEFAULT_BRANCH}/protection"
        )
        if not isinstance(protection, dict):
            raise VerificationError("github", "protection_response_shape_is_invalid")
    else:
        protection = {}

    required_checks = protection.get("required_status_checks")
    checks_strict = isinstance(required_checks, dict) and required_checks.get("strict") is True
    required_contexts = _status_contexts(required_checks)
    required_contexts_match = required_contexts == set(REQUIRED_STATUS_CONTEXTS)
    validate_required = "validate" in required_contexts
    artifact_compatibility_required = (
        "Round-trip synthetic review evidence" in required_contexts
    )
    if not checks_strict:
        _add_finding(findings, "required_checks_are_not_strict", DEFAULT_BRANCH)
    if not validate_required:
        _add_finding(findings, "validate_check_is_not_required", DEFAULT_BRANCH)
    if not artifact_compatibility_required:
        _add_finding(
            findings,
            "artifact_compatibility_check_is_not_required",
            DEFAULT_BRANCH,
        )
    if not required_contexts_match:
        _add_finding(findings, "required_status_contexts_drift", DEFAULT_BRANCH)
    if not _enabled(protection.get("enforce_admins")):
        _add_finding(findings, "administrator_enforcement_is_disabled", DEFAULT_BRANCH)
    if not _enabled(protection.get("required_linear_history")):
        _add_finding(findings, "linear_history_is_not_required", DEFAULT_BRANCH)
    if _enabled(protection.get("allow_force_pushes")):
        _add_finding(findings, "force_pushes_are_allowed", DEFAULT_BRANCH)
    if _enabled(protection.get("allow_deletions")):
        _add_finding(findings, "branch_deletion_is_allowed", DEFAULT_BRANCH)
    if not _enabled(protection.get("required_conversation_resolution")):
        _add_finding(findings, "conversation_resolution_is_not_required", DEFAULT_BRANCH)

    reviews = protection.get("required_pull_request_reviews")
    reviews_valid = isinstance(reviews, dict)
    actual_approvals = reviews.get("required_approving_review_count") if reviews_valid else None
    dismiss_stale = reviews.get("dismiss_stale_reviews") is True if reviews_valid else False
    last_push_approval = reviews.get("require_last_push_approval") is True if reviews_valid else False
    if actual_approvals != required_approvals:
        _add_finding(findings, "required_approval_count_drift", DEFAULT_BRANCH)
    if not dismiss_stale:
        _add_finding(findings, "stale_reviews_are_not_dismissed", DEFAULT_BRANCH)
    if last_push_approval:
        _add_finding(findings, "last_push_approval_is_unexpected", DEFAULT_BRANCH)

    environment_results: list[dict[str, Any]] = []
    for environment in REQUIRED_ENVIRONMENTS:
        expected = config.environments[environment]
        scope = f"environment:{environment}"
        environment_findings_before = len(findings)
        environment_path = f"{repository_path}/environments/{environment}"
        environment_state = client.get(environment_path)
        if not isinstance(environment_state, dict):
            raise VerificationError("github", "environment_response_shape_is_invalid")
        branch_policy = environment_state.get("deployment_branch_policy")
        custom_policy_enabled = (
            isinstance(branch_policy, dict)
            and branch_policy.get("custom_branch_policies") is True
            and branch_policy.get("protected_branches") is False
        )
        if not custom_policy_enabled:
            _add_finding(findings, "environment_branch_policy_drift", scope)

        policies = client.list_all(
            f"{environment_path}/deployment-branch-policies",
            "branch_policies",
        )
        policy_names = {
            str(item.get("name"))
            for item in policies
            if isinstance(item.get("name"), str)
        }
        if policy_names != {DEFAULT_BRANCH}:
            _add_finding(findings, "environment_branch_scope_drift", scope)

        variables = client.list_all(
            f"/repositories/{repository_id}/environments/{environment}/variables",
            "variables",
        )
        variable_values: dict[str, str] = {}
        duplicate_names: set[str] = set()
        for item in variables:
            name = item.get("name")
            value = item.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            if name in variable_values:
                duplicate_names.add(name)
            variable_values[name] = value
        if duplicate_names:
            _add_finding(findings, "duplicate_environment_variable", scope)

        expected_variables = {
            "DATABRICKS_HOST": expected.databricks_host,
            "DATABRICKS_CLIENT_ID": expected.deployment_client_id,
            "DATABRICKS_RUNTIME_CLIENT_ID": expected.runtime_client_id,
        }
        variable_matches: dict[str, bool] = {}
        for name, expected_value in expected_variables.items():
            matches = variable_values.get(name) == expected_value
            variable_matches[name] = matches
            if not matches:
                _add_finding(findings, "environment_variable_drift", f"{scope}:{name}")
        if FORBIDDEN_STATIC_SECRET in variable_values:
            _add_finding(findings, "static_client_secret_variable_present", scope)

        secrets = client.list_all(
            f"/repositories/{repository_id}/environments/{environment}/secrets",
            "secrets",
        )
        secret_names = {
            str(item.get("name"))
            for item in secrets
            if isinstance(item.get("name"), str)
        }
        static_secret_absent = FORBIDDEN_STATIC_SECRET not in secret_names
        if not static_secret_absent:
            _add_finding(findings, "static_client_secret_present", scope)

        environment_results.append(
            {
                "environment": environment,
                "verified": len(findings) == environment_findings_before,
                "custom_main_only_policy": custom_policy_enabled
                and policy_names == {DEFAULT_BRANCH},
                "variables": variable_matches,
                "static_client_secret_absent": static_secret_absent,
                "host_fingerprint": _fingerprint(expected.databricks_host),
                "deployment_client_id_fingerprint": _fingerprint(
                    expected.deployment_client_id
                ),
                "runtime_client_id_fingerprint": _fingerprint(
                    expected.runtime_client_id
                ),
            }
        )

    return {
        "schema_version": 1,
        "status": "verified" if not findings else "blocked",
        "generated_at_utc": _utc_now(),
        "repository": config.repository,
        "branch": DEFAULT_BRANCH,
        "branch_head_sha": branch_head_sha,
        "main_protected": main_protected,
        "required_approvals": required_approvals,
        "repository_settings": repository_setting_results,
        "branch_protection": {
            "required_checks_strict": checks_strict,
            "expected_status_contexts": list(REQUIRED_STATUS_CONTEXTS),
            "required_status_contexts": sorted(required_contexts),
            "required_status_contexts_match": required_contexts_match,
            "validate_required": validate_required,
            "artifact_compatibility_required": artifact_compatibility_required,
            "administrator_enforcement": _enabled(protection.get("enforce_admins")),
            "linear_history": _enabled(protection.get("required_linear_history")),
            "force_pushes_blocked": not _enabled(protection.get("allow_force_pushes")),
            "deletion_blocked": not _enabled(protection.get("allow_deletions")),
            "conversation_resolution": _enabled(
                protection.get("required_conversation_resolution")
            ),
            "required_approving_review_count": actual_approvals,
            "dismiss_stale_reviews": dismiss_stale,
        },
        "environments": environment_results,
        "findings": findings,
    }


def render_summary(evidence: Mapping[str, Any]) -> str:
    findings = evidence.get("findings", [])
    lines = [
        "# GitHub governance verification",
        "",
        f"- Status: **{evidence.get('status', 'unknown')}**",
        f"- Repository: `{evidence.get('repository', '')}`",
        f"- Branch: `{evidence.get('branch', '')}`",
        f"- Required approvals: `{evidence.get('required_approvals', '')}`",
        f"- Findings: `{len(findings) if isinstance(findings, list) else 'unknown'}`",
    ]
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            lines.append(
                f"  - `{finding.get('category', '')}` in `{finding.get('scope', '')}`"
            )
    lines.extend(
        [
            "",
            "The evidence contains expected-value fingerprints and stable drift categories,",
            "not repository tokens, workspace hosts, client IDs, runtime IDs, or secret values.",
            "Verification is read-only and does not change GitHub or Databricks state.",
            "",
        ]
    )
    return "\n".join(lines)


def capture_verification(
    *,
    config: VerificationConfig,
    client: ReadOnlyGitHub,
    output_directory: Path,
    required_approvals: int,
) -> int:
    prepared = _prepare_output_directory(output_directory)
    try:
        evidence = verify_state(
            config,
            client=client,
            required_approvals=required_approvals,
        )
    except VerificationError as error:
        evidence = {
            "schema_version": 1,
            "status": "failed",
            "generated_at_utc": _utc_now(),
            "repository": config.repository,
            "branch": DEFAULT_BRANCH,
            "failure": {"stage": error.stage, "category": error.category},
            "findings": [],
        }
    _write_json_atomic(prepared / "github-governance-verification.json", evidence)
    _write_text_atomic(
        prepared / "github-governance-verification.md",
        render_summary(evidence),
    )
    return 0 if evidence["status"] == "verified" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-config", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--required-approvals", type=int, choices=(0, 1), default=0
    )
    parser.add_argument(
        "--timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.github_config, args.runtime_config)
        client = GitHubClient(
            os.environ.get("GITHUB_ADMIN_TOKEN", ""),
            timeout_seconds=args.timeout_seconds,
        )
        return capture_verification(
            config=config,
            client=client,
            output_directory=args.output_dir,
            required_approvals=args.required_approvals,
        )
    except (ValueError, VerificationError) as error:
        category = (
            error.category if isinstance(error, VerificationError) else "configuration_is_invalid"
        )
        print(f"GitHub governance verification failed: {category}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
