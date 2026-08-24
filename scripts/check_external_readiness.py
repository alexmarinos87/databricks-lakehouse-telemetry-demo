#!/usr/bin/env python3
"""Capture sanitized external-control readiness before Databricks CLI use.

The preflight is intentionally read-only. It verifies that the checked-out main
commit is current, GitHub reports active protection with the required validation
context, and the workflow exposes the expected environment-scoped GitHub OIDC
configuration. It never authenticates to Databricks, installs software, deploys,
uploads data, executes SQL, or mutates permissions.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_BRANCH = "main"
DEFAULT_API_URL = "https://api.github.com"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
REQUIRED_STATUS_CONTEXT = "validate"
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_REQUIRED_GITHUB_OIDC_CONTEXT = (
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "GITHUB_REPOSITORY",
    "GITHUB_REF",
    "GITHUB_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
)


class ReadinessError(RuntimeError):
    """A bounded failure that is safe to persist without provider diagnostics."""

    def __init__(self, stage: str, category: str) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage = stage
        self.category = category


def positive_seconds(value: str) -> float:
    """Parse a finite positive request deadline."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(
            "must be a finite positive number of seconds"
        )
    return parsed


def validate_repository(value: str) -> str:
    repository = value.strip()
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise argparse.ArgumentTypeError("repository must use owner/name form")
    return repository


def validate_branch(value: str) -> str:
    branch = value.strip()
    if (
        not branch
        or len(branch.encode("utf-8")) > 255
        or any(character in branch for character in ("\x00", "\n", "\r"))
    ):
        raise argparse.ArgumentTypeError("branch is invalid")
    return branch


def validate_sha(value: str) -> str:
    sha = value.strip().lower()
    if not _SHA_PATTERN.fullmatch(sha):
        raise argparse.ArgumentTypeError("accepted SHA must contain 40 hexadecimal characters")
    return sha


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise ReadinessError("configuration", "output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ReadinessError(
            "configuration", "output_directory_could_not_be_created"
        ) from None
    if path.is_symlink() or not path.is_dir():
        raise ReadinessError(
            "configuration", "output_directory_is_not_regular"
        )
    return path


def _validate_api_url(value: str) -> str:
    api_url = value.strip().rstrip("/")
    if api_url != DEFAULT_API_URL:
        raise ReadinessError("configuration", "github_api_url_is_not_allowed")
    return api_url


def _required_status_contexts(payload: Mapping[str, Any]) -> list[str]:
    protection = payload.get("protection")
    if not isinstance(protection, dict):
        return []
    required = protection.get("required_status_checks")
    if not isinstance(required, dict):
        return []

    contexts: set[str] = set()
    raw_contexts = required.get("contexts")
    if isinstance(raw_contexts, list):
        contexts.update(
            item.strip()
            for item in raw_contexts
            if isinstance(item, str) and item.strip()
        )
    raw_checks = required.get("checks")
    if isinstance(raw_checks, list):
        for item in raw_checks:
            if not isinstance(item, dict):
                continue
            context = item.get("context")
            if isinstance(context, str) and context.strip():
                contexts.add(context.strip())
    return sorted(contexts)


def fetch_branch_state(
    *,
    repository: str,
    branch: str,
    api_url: str,
    token: str,
    timeout_seconds: float,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read one branch endpoint without exposing token or provider diagnostics."""

    normalized_api_url = _validate_api_url(api_url)
    owner, repository_name = repository.split("/", 1)
    path = "/repos/{owner}/{repository}/branches/{branch}".format(
        owner=urllib.parse.quote(owner, safe=""),
        repository=urllib.parse.quote(repository_name, safe=""),
        branch=urllib.parse.quote(branch, safe=""),
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "lakehouse-demo-external-readiness",
    }
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = urllib.request.Request(
        normalized_api_url + path,
        method="GET",
        headers=headers,
    )
    request_opener = opener or urllib.request.urlopen
    try:
        with request_opener(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            response_body = response.read()
    except urllib.error.HTTPError:
        raise ReadinessError("github", "branch_request_failed") from None
    except (OSError, TimeoutError, urllib.error.URLError):
        raise ReadinessError("github", "branch_request_failed") from None

    if status != 200:
        raise ReadinessError("github", "branch_request_failed")
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReadinessError("github", "branch_response_is_invalid_json") from None
    if not isinstance(payload, dict):
        raise ReadinessError("github", "branch_response_shape_is_invalid")

    commit = payload.get("commit")
    head_sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(head_sha, str) or not _SHA_PATTERN.fullmatch(head_sha):
        raise ReadinessError("github", "branch_head_sha_is_missing")

    contexts = _required_status_contexts(payload)
    return {
        "head_sha": head_sha,
        "protected": payload.get("protected") is True,
        "required_status_contexts": contexts,
        "validate_required": REQUIRED_STATUS_CONTEXT in contexts,
    }


def _valid_databricks_host(value: str) -> bool:
    if len(value.encode("utf-8")) > 512:
        return False
    parsed = urllib.parse.urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in ("", "/")
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _valid_client_id(value: str) -> bool:
    return (
        bool(value)
        and len(value.encode("utf-8")) <= 256
        and not any(character.isspace() for character in value)
        and not any(character in value for character in ("\x00", "\n", "\r"))
    )


def build_evidence(
    *,
    repository: str,
    branch: str,
    accepted_sha: str,
    environment: Mapping[str, str],
    branch_state: Mapping[str, Any] | None,
    branch_error: ReadinessError | None = None,
) -> dict[str, Any]:
    """Evaluate every external gate and return a deterministic sanitized record."""

    blockers: list[str] = []
    github: dict[str, Any] = {
        "accepted_sha": accepted_sha,
        "branch": branch,
        "branch_state_available": branch_state is not None,
    }
    if branch_state is None:
        blockers.append("github_branch_state_unavailable")
        if branch_error is not None:
            github["failure"] = {
                "stage": branch_error.stage,
                "category": branch_error.category,
            }
    else:
        head_sha = str(branch_state["head_sha"])
        protected = branch_state.get("protected") is True
        validate_required = branch_state.get("validate_required") is True
        accepted_matches_head = accepted_sha == head_sha
        github.update(
            {
                "head_sha": head_sha,
                "accepted_sha_matches_head": accepted_matches_head,
                "protected": protected,
                "required_status_contexts": list(
                    branch_state.get("required_status_contexts", [])
                ),
                "validate_required": validate_required,
            }
        )
        if not accepted_matches_head:
            blockers.append("accepted_main_is_stale")
        if not protected:
            blockers.append("main_branch_is_unprotected")
        if not validate_required:
            blockers.append("validate_check_is_not_required")

    github_actions_context = environment.get("GITHUB_ACTIONS") == "true"
    if not github_actions_context:
        blockers.append("github_actions_context_is_missing")

    workflow_repository = environment.get("GITHUB_REPOSITORY", "").strip()
    workflow_repository_matches = workflow_repository == repository
    if not workflow_repository_matches:
        blockers.append("github_repository_does_not_match")

    workflow_sha = environment.get("GITHUB_SHA", "").strip().lower()
    workflow_sha_valid = bool(_SHA_PATTERN.fullmatch(workflow_sha))
    accepted_sha_matches_workflow_sha = (
        workflow_sha_valid and accepted_sha == workflow_sha
    )
    if not workflow_sha_valid:
        blockers.append("github_sha_is_invalid")
    elif not accepted_sha_matches_workflow_sha:
        blockers.append("accepted_main_does_not_match_workflow_sha")

    auth_type_is_github_oidc = (
        environment.get("DATABRICKS_AUTH_TYPE", "").strip() == "github-oidc"
    )
    if not auth_type_is_github_oidc:
        blockers.append("databricks_auth_type_is_not_github_oidc")

    host = environment.get("DATABRICKS_HOST", "").strip().rstrip("/")
    host_configured = bool(host)
    host_valid = host_configured and _valid_databricks_host(host)
    if not host_configured:
        blockers.append("databricks_host_is_missing")
    elif not host_valid:
        blockers.append("databricks_host_is_invalid")

    client_id = environment.get("DATABRICKS_CLIENT_ID", "").strip()
    client_id_configured = bool(client_id)
    client_id_valid = client_id_configured and _valid_client_id(client_id)
    if not client_id_configured:
        blockers.append("databricks_client_id_is_missing")
    elif not client_id_valid:
        blockers.append("databricks_client_id_is_invalid")

    static_client_secret_present = bool(
        environment.get("DATABRICKS_CLIENT_SECRET", "").strip()
    )
    if static_client_secret_present:
        blockers.append("static_client_secret_is_present")

    missing_oidc_context = [
        name
        for name in _REQUIRED_GITHUB_OIDC_CONTEXT
        if not environment.get(name, "").strip()
    ]
    oidc_context_complete = not missing_oidc_context
    if not oidc_context_complete:
        blockers.append("github_oidc_context_is_incomplete")

    expected_ref = f"refs/heads/{branch}"
    github_ref_is_expected = environment.get("GITHUB_REF", "").strip() == expected_ref
    if not github_ref_is_expected:
        blockers.append("github_ref_is_not_expected_branch")

    evidence = {
        "schema_version": 1,
        "status": "ready" if not blockers else "blocked",
        "generated_at_utc": _utc_now(),
        "repository": repository,
        "github": {
            **github,
            "github_actions_context": github_actions_context,
            "workflow_repository_matches": workflow_repository_matches,
            "workflow_sha_valid": workflow_sha_valid,
            "workflow_sha": workflow_sha if workflow_sha_valid else None,
            "accepted_sha_matches_workflow_sha": accepted_sha_matches_workflow_sha,
            "github_ref_is_expected_branch": github_ref_is_expected,
            "oidc_context_complete": oidc_context_complete,
            "missing_oidc_context": missing_oidc_context,
        },
        "databricks": {
            "auth_type_is_github_oidc": auth_type_is_github_oidc,
            "host_configured": host_configured,
            "host_valid": host_valid,
            "host_fingerprint": _fingerprint(host),
            "client_id_configured": client_id_configured,
            "client_id_valid": client_id_valid,
            "client_id_fingerprint": _fingerprint(client_id),
            "static_client_secret_present": static_client_secret_present,
        },
        "blockers": blockers,
    }
    return evidence


def render_summary(evidence: Mapping[str, Any]) -> str:
    """Render a bounded summary containing no raw workspace or identity values."""

    github = evidence.get("github", {})
    databricks = evidence.get("databricks", {})
    blockers = evidence.get("blockers", [])
    blocker_text = ", ".join(f"`{item}`" for item in blockers) or "none"
    lines = [
        "# External readiness evidence",
        "",
        f"- Status: **{evidence.get('status', 'unknown')}**",
        f"- Repository: `{evidence.get('repository', '')}`",
        f"- Branch: `{github.get('branch', '')}`",
        f"- Accepted commit: `{github.get('accepted_sha', '')}`",
        f"- Accepted commit is current: `{github.get('accepted_sha_matches_head', False)}`",
        f"- Workflow repository matches: `{github.get('workflow_repository_matches', False)}`",
        f"- Workflow commit matches accepted checkout: `{github.get('accepted_sha_matches_workflow_sha', False)}`",
        f"- Main protected: `{github.get('protected', False)}`",
        f"- Required `validate` context active: `{github.get('validate_required', False)}`",
        f"- GitHub OIDC context complete: `{github.get('oidc_context_complete', False)}`",
        f"- Databricks host configured and valid: `{databricks.get('host_configured', False) and databricks.get('host_valid', False)}`",
        f"- Databricks client ID configured and valid: `{databricks.get('client_id_configured', False) and databricks.get('client_id_valid', False)}`",
        f"- Static client secret present: `{databricks.get('static_client_secret_present', False)}`",
        f"- Blockers: {blocker_text}",
        "",
        "The evidence records booleans, fingerprints, commit identities, and blocker categories only.",
        "A blocked preflight prevents Databricks CLI installation and all Databricks commands.",
        "A ready preflight permits plan evidence collection but does not authorize deployment or mutation.",
        "",
    ]
    return "\n".join(lines)


def capture_readiness(
    *,
    repository: str,
    branch: str,
    accepted_sha: str,
    output_directory: Path,
    environment: Mapping[str, str],
    api_url: str,
    request_timeout_seconds: float,
    opener: Callable[..., Any] | None = None,
) -> int:
    output_directory = _prepare_output_directory(output_directory)
    branch_state: dict[str, Any] | None = None
    branch_error: ReadinessError | None = None
    try:
        branch_state = fetch_branch_state(
            repository=repository,
            branch=branch,
            api_url=api_url,
            token=environment.get("GITHUB_TOKEN", ""),
            timeout_seconds=request_timeout_seconds,
            opener=opener,
        )
    except ReadinessError as error:
        branch_error = error

    evidence = build_evidence(
        repository=repository,
        branch=branch,
        accepted_sha=accepted_sha,
        environment=environment,
        branch_state=branch_state,
        branch_error=branch_error,
    )
    _write_json_atomic(output_directory / "external-readiness.json", evidence)
    _write_text_atomic(
        output_directory / "external-readiness-summary.md",
        render_summary(evidence),
    )
    return 0 if evidence["status"] == "ready" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=validate_repository)
    parser.add_argument("--branch", default=DEFAULT_BRANCH, type=validate_branch)
    parser.add_argument("--accepted-sha", required=True, type=validate_sha)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL),
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return capture_readiness(
            repository=args.repository,
            branch=args.branch,
            accepted_sha=args.accepted_sha,
            output_directory=args.output_dir,
            environment=os.environ,
            api_url=args.api_url,
            request_timeout_seconds=args.request_timeout_seconds,
        )
    except ReadinessError as error:
        print(
            f"External readiness failed during {error.stage}: {error.category}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
