#!/usr/bin/env python3
"""Verify that an accepted Git commit has the required successful GitHub checks.

This verifier is intentionally read-only. It queries one bounded GitHub Checks
inventory for the exact accepted commit, persists only sanitized check metadata,
and never authenticates to Databricks or mutates repository state.
"""

from __future__ import annotations

import argparse
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


DEFAULT_API_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100
MAX_RESPONSE_BYTES = 1_000_000
EXPECTED_APP_SLUG = "github-actions"
REQUIRED_STATUS_CONTEXTS = (
    "validate",
    "Round-trip synthetic review evidence",
)
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_FIELD_PATTERN = re.compile(r"[A-Za-z0-9_.:/ -]{1,256}\Z")


class CheckRunError(RuntimeError):
    """A bounded failure safe to persist without raw provider diagnostics."""

    def __init__(self, stage: str, category: str) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage = stage
        self.category = category


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


def validate_repository(value: str) -> str:
    repository = value.strip()
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise argparse.ArgumentTypeError("repository must use owner/name form")
    return repository


def validate_sha(value: str) -> str:
    commit_sha = value.strip().lower()
    if not _SHA_PATTERN.fullmatch(commit_sha):
        raise argparse.ArgumentTypeError(
            "commit must contain 40 hexadecimal characters"
        )
    return commit_sha


def _validate_api_url(value: str) -> str:
    api_url = value.strip().rstrip("/")
    if api_url != DEFAULT_API_URL:
        raise CheckRunError("configuration", "github_api_url_is_not_allowed")
    return api_url


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
        raise CheckRunError("configuration", "output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CheckRunError(
            "configuration", "output_directory_could_not_be_created"
        ) from None
    if path.is_symlink() or not path.is_dir():
        raise CheckRunError(
            "configuration", "output_directory_is_not_regular"
        )
    return path


def _safe_field(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not _SAFE_FIELD_PATTERN.fullmatch(cleaned):
        return None
    return cleaned


def fetch_check_runs(
    *,
    repository: str,
    commit_sha: str,
    api_url: str,
    token: str,
    timeout_seconds: float,
    opener: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch one complete, bounded latest-check inventory for an exact commit."""

    normalized_api_url = _validate_api_url(api_url)
    if not token.strip():
        raise CheckRunError("configuration", "github_token_is_missing")
    owner, repository_name = repository.split("/", 1)
    query = urllib.parse.urlencode(
        {"filter": "latest", "per_page": PAGE_SIZE}
    )
    path = (
        "/repos/{owner}/{repository}/commits/{commit}/check-runs?{query}"
    ).format(
        owner=urllib.parse.quote(owner, safe=""),
        repository=urllib.parse.quote(repository_name, safe=""),
        commit=urllib.parse.quote(commit_sha, safe=""),
        query=query,
    )
    request = urllib.request.Request(
        normalized_api_url + path,
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token.strip()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lakehouse-demo-main-check-verifier",
        },
    )
    request_opener = opener or urllib.request.urlopen
    try:
        with request_opener(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError:
        raise CheckRunError("github", "check_run_request_failed") from None
    except (OSError, TimeoutError, urllib.error.URLError):
        raise CheckRunError("github", "check_run_request_failed") from None

    if status != 200:
        raise CheckRunError("github", "check_run_request_failed")
    if len(response_body) > MAX_RESPONSE_BYTES:
        raise CheckRunError("github", "check_run_response_is_too_large")
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckRunError(
            "github", "check_run_response_is_invalid_json"
        ) from None
    if not isinstance(payload, dict):
        raise CheckRunError("github", "check_run_response_shape_is_invalid")
    total_count = payload.get("total_count")
    raw_runs = payload.get("check_runs")
    if not isinstance(total_count, int) or total_count < 0:
        raise CheckRunError("github", "check_run_response_shape_is_invalid")
    if not isinstance(raw_runs, list) or any(
        not isinstance(item, dict) for item in raw_runs
    ):
        raise CheckRunError("github", "check_run_response_shape_is_invalid")
    if total_count != len(raw_runs) or total_count > PAGE_SIZE:
        raise CheckRunError("github", "check_run_inventory_is_truncated")
    return list(raw_runs)


def build_evidence(
    *,
    repository: str,
    commit_sha: str,
    check_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate every required check and retain only bounded metadata."""

    blockers: list[str] = []
    required_results: list[dict[str, Any]] = []

    for required_name in REQUIRED_STATUS_CONTEXTS:
        matches = [
            item
            for item in check_runs
            if item.get("name") == required_name
        ]
        result: dict[str, Any] = {
            "name": required_name,
            "observed_count": len(matches),
            "verified": False,
        }
        if not matches:
            blockers.append(f"required_check_run_is_missing:{required_name}")
            required_results.append(result)
            continue
        if len(matches) != 1:
            blockers.append(f"required_check_run_is_ambiguous:{required_name}")
            required_results.append(result)
            continue

        check_run = matches[0]
        status = _safe_field(check_run.get("status"))
        conclusion = _safe_field(check_run.get("conclusion"))
        head_sha = _safe_field(check_run.get("head_sha"))
        app = check_run.get("app")
        app_slug = _safe_field(app.get("slug")) if isinstance(app, dict) else None

        head_sha_matches = head_sha == commit_sha
        app_is_expected = app_slug == EXPECTED_APP_SLUG
        completed = status == "completed"
        successful = conclusion == "success"
        verified = (
            head_sha_matches
            and app_is_expected
            and completed
            and successful
        )
        result.update(
            {
                "status": status,
                "conclusion": conclusion,
                "app_slug": app_slug,
                "head_sha_matches": head_sha_matches,
                "app_is_expected": app_is_expected,
                "completed": completed,
                "successful": successful,
                "verified": verified,
            }
        )

        if not head_sha_matches:
            blockers.append(
                f"required_check_run_commit_mismatch:{required_name}"
            )
        if not app_is_expected:
            blockers.append(
                f"required_check_run_app_is_unexpected:{required_name}"
            )
        if not completed:
            blockers.append(
                f"required_check_run_is_not_completed:{required_name}"
            )
        if not successful:
            blockers.append(
                f"required_check_run_is_not_successful:{required_name}"
            )
        required_results.append(result)

    return {
        "schema_version": 1,
        "status": "verified" if not blockers else "blocked",
        "generated_at_utc": _utc_now(),
        "repository": repository,
        "commit_sha": commit_sha,
        "expected_app_slug": EXPECTED_APP_SLUG,
        "required_status_contexts": list(REQUIRED_STATUS_CONTEXTS),
        "required_checks": required_results,
        "blockers": blockers,
    }


def render_summary(evidence: Mapping[str, Any]) -> str:
    required_checks = evidence.get("required_checks", [])
    blockers = evidence.get("blockers", [])
    lines = [
        "# Accepted-main delivery-check verification",
        "",
        f"- Status: **{evidence.get('status', 'unknown')}**",
        f"- Repository: `{evidence.get('repository', '')}`",
        f"- Commit: `{evidence.get('commit_sha', '')}`",
        f"- Expected check app: `{evidence.get('expected_app_slug', '')}`",
        f"- Blockers: `{len(blockers) if isinstance(blockers, list) else 'unknown'}`",
    ]
    if isinstance(required_checks, list):
        for item in required_checks:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- `{name}`: verified=`{verified}`, status=`{status}`, "
                "conclusion=`{conclusion}`, app=`{app}`".format(
                    name=item.get("name", ""),
                    verified=item.get("verified", False),
                    status=item.get("status", "unavailable"),
                    conclusion=item.get("conclusion", "unavailable"),
                    app=item.get("app_slug", "unavailable"),
                )
            )
    if isinstance(blockers, list):
        for blocker in blockers:
            if isinstance(blocker, str):
                lines.append(f"  - `{blocker}`")
    lines.extend(
        [
            "",
            "The evidence contains check names, bounded states, the accepted commit,",
            "and stable blocker categories. It excludes tokens, provider diagnostics,",
            "check output, annotations, URLs, and Databricks configuration.",
            "Verification is read-only and does not authorize deployment.",
            "",
        ]
    )
    return "\n".join(lines)


def capture_verification(
    *,
    repository: str,
    commit_sha: str,
    output_directory: Path,
    environment: Mapping[str, str],
    api_url: str,
    timeout_seconds: float,
    opener: Callable[..., Any] | None = None,
) -> int:
    prepared = _prepare_output_directory(output_directory)
    try:
        check_runs = fetch_check_runs(
            repository=repository,
            commit_sha=commit_sha,
            api_url=api_url,
            token=environment.get("GITHUB_TOKEN", ""),
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        evidence = build_evidence(
            repository=repository,
            commit_sha=commit_sha,
            check_runs=check_runs,
        )
    except CheckRunError as error:
        evidence = {
            "schema_version": 1,
            "status": "failed",
            "generated_at_utc": _utc_now(),
            "repository": repository,
            "commit_sha": commit_sha,
            "expected_app_slug": EXPECTED_APP_SLUG,
            "required_status_contexts": list(REQUIRED_STATUS_CONTEXTS),
            "required_checks": [],
            "failure": {
                "stage": error.stage,
                "category": error.category,
            },
            "blockers": [error.category],
        }

    _write_json_atomic(
        prepared / "main-check-runs-verification.json",
        evidence,
    )
    _write_text_atomic(
        prepared / "main-check-runs-verification.md",
        render_summary(evidence),
    )
    return 0 if evidence["status"] == "verified" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=validate_repository)
    parser.add_argument("--commit", required=True, type=validate_sha)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL),
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
        return capture_verification(
            repository=args.repository,
            commit_sha=args.commit,
            output_directory=args.output_dir,
            environment=os.environ,
            api_url=args.api_url,
            timeout_seconds=args.timeout_seconds,
        )
    except CheckRunError as error:
        print(
            f"Accepted-main check verification failed during "
            f"{error.stage}: {error.category}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
