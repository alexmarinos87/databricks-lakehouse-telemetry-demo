#!/usr/bin/env python3
"""Capture bounded Databricks OIDC identity and bundle-plan evidence.

The script intentionally does not deploy, run a workflow, upload data, execute SQL,
or mutate permissions. It verifies the short-lived GitHub OIDC identity, records
sanitized provenance, and optionally captures successful bundle validation and plan
output for human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_IDENTITY_TIMEOUT_SECONDS = 60.0
DEFAULT_VALIDATE_TIMEOUT_SECONDS = 180.0
DEFAULT_PLAN_TIMEOUT_SECONDS = 300.0
MAX_BUNDLE_VARIABLES = 32
MAX_BUNDLE_VARIABLE_BYTES = 2_048
MAX_CAPTURE_BYTES = 4_000_000
_ALLOWED_TARGETS = {"dev", "prod"}
_BUNDLE_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_GITHUB_CONTEXT = (
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "GITHUB_REPOSITORY",
    "GITHUB_REF",
    "GITHUB_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
)


class EvidenceError(RuntimeError):
    """A bounded failure that can be persisted without provider diagnostics."""

    def __init__(
        self,
        stage: str,
        category: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage = stage
        self.category = category
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def positive_seconds(value: str) -> float:
    """Parse a finite positive command deadline."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return parsed


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text_metadata(value: str) -> dict[str, int | str]:
    encoded = value.encode("utf-8", errors="replace")
    return {
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise EvidenceError("configuration", "output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise EvidenceError("configuration", "output_directory_could_not_be_created") from None
    if path.is_symlink() or not path.is_dir():
        raise EvidenceError("configuration", "output_directory_is_not_regular")
    return path


def normalize_bundle_variables(values: Sequence[str]) -> tuple[str, ...]:
    """Validate and canonicalize bounded Databricks bundle variable arguments."""

    if len(values) > MAX_BUNDLE_VARIABLES:
        raise EvidenceError("configuration", "too_many_bundle_variables")

    normalized: list[str] = []
    seen_names: set[str] = set()
    for value in values:
        if len(value.encode("utf-8")) > MAX_BUNDLE_VARIABLE_BYTES:
            raise EvidenceError("configuration", "bundle_variable_too_large")
        if any(character in value for character in ("\x00", "\n", "\r")):
            raise EvidenceError("configuration", "bundle_variable_contains_control_character")
        if "=" not in value:
            raise EvidenceError("configuration", "bundle_variable_missing_separator")
        name, variable_value = value.split("=", 1)
        if not _BUNDLE_VARIABLE_NAME.fullmatch(name):
            raise EvidenceError("configuration", "bundle_variable_name_is_invalid")
        if name in seen_names:
            raise EvidenceError("configuration", "duplicate_bundle_variable")
        if not variable_value:
            raise EvidenceError("configuration", "bundle_variable_value_is_blank")
        seen_names.add(name)
        normalized.append(f"{name}={variable_value}")

    return tuple(normalized)


def validate_environment(environment: Mapping[str, str], target: str) -> None:
    """Fail closed unless the workflow exposes the expected GitHub OIDC context."""

    if target not in _ALLOWED_TARGETS:
        raise EvidenceError("configuration", "unsupported_target")
    if environment.get("GITHUB_ACTIONS") != "true":
        raise EvidenceError("configuration", "github_actions_context_is_missing")
    if environment.get("DATABRICKS_AUTH_TYPE") != "github-oidc":
        raise EvidenceError("configuration", "databricks_auth_type_is_not_github_oidc")
    if not environment.get("DATABRICKS_HOST", "").strip():
        raise EvidenceError("configuration", "databricks_host_is_missing")
    if not environment.get("DATABRICKS_CLIENT_ID", "").strip():
        raise EvidenceError("configuration", "databricks_client_id_is_missing")
    if environment.get("DATABRICKS_CLIENT_SECRET", "").strip():
        raise EvidenceError("configuration", "static_client_secret_is_present")

    missing = [name for name in _REQUIRED_GITHUB_CONTEXT if not environment.get(name, "").strip()]
    if missing:
        raise EvidenceError("configuration", "github_oidc_context_is_incomplete")


def _run_command(
    command: Sequence[str],
    *,
    stage: str,
    timeout_seconds: float,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run one child process without echoing command arguments or output on failure."""

    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=dict(environment),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        raise EvidenceError(
            stage,
            "command_timed_out",
            stdout=stdout,
            stderr=stderr,
        ) from None
    except OSError:
        raise EvidenceError(stage, "command_could_not_be_started") from None

    if completed.returncode != 0:
        raise EvidenceError(
            stage,
            "command_failed",
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _first_string(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def verify_identity(
    environment: Mapping[str, str],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Prove that unified authentication resolved the configured service principal."""

    completed = _run_command(
        ["databricks", "current-user", "me", "-o", "json"],
        stage="identity",
        timeout_seconds=timeout_seconds,
        environment=environment,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        raise EvidenceError(
            "identity",
            "invalid_json_response",
            stdout=completed.stdout,
            stderr=completed.stderr,
        ) from None
    if not isinstance(payload, dict):
        raise EvidenceError("identity", "unexpected_response_shape")

    active = payload.get("active", True)
    if active is False:
        raise EvidenceError("identity", "authenticated_principal_is_inactive")

    expected_client_id = environment["DATABRICKS_CLIENT_ID"].strip()
    application_id = _first_string(payload, "application_id", "applicationId")
    if application_id is None:
        user_name = _first_string(payload, "user_name", "userName")
        if user_name == expected_client_id:
            application_id = user_name
    if application_id is None:
        raise EvidenceError("identity", "authenticated_principal_is_not_a_service_principal")
    if application_id != expected_client_id:
        raise EvidenceError("identity", "authenticated_identity_does_not_match_client_id")

    principal_id = _first_string(payload, "id")
    if principal_id is None:
        raise EvidenceError("identity", "authenticated_principal_id_is_missing")

    return {
        "status": "succeeded",
        "active": active is not False,
        "application_id_fingerprint": _fingerprint(application_id),
        "principal_id_fingerprint": _fingerprint(principal_id),
    }


def _capture_successful_output(
    output_directory: Path,
    stage: str,
    completed: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    stdout_bytes = len(completed.stdout.encode("utf-8", errors="replace"))
    stderr_bytes = len(completed.stderr.encode("utf-8", errors="replace"))
    if stdout_bytes + stderr_bytes > MAX_CAPTURE_BYTES:
        raise EvidenceError(
            stage,
            "successful_output_exceeded_limit",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    output_file = f"bundle-{stage}.txt"
    _write_text_atomic(output_directory / output_file, completed.stdout)
    result: dict[str, Any] = {
        "status": "succeeded",
        "output_file": output_file,
        "output_bytes": stdout_bytes,
        "output_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
    }
    if completed.stderr:
        warnings_file = f"bundle-{stage}-warnings.txt"
        _write_text_atomic(output_directory / warnings_file, completed.stderr)
        result.update(
            {
                "warnings_file": warnings_file,
                "warnings_bytes": stderr_bytes,
                "warnings_sha256": hashlib.sha256(
                    completed.stderr.encode("utf-8")
                ).hexdigest(),
            }
        )
    return result


def capture_bundle_stage(
    stage: str,
    target: str,
    bundle_variables: Sequence[str],
    output_directory: Path,
    environment: Mapping[str, str],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    command = ["databricks", "bundle", stage, "--target", target]
    for variable in bundle_variables:
        command.extend(["--var", variable])
    completed = _run_command(
        command,
        stage=stage,
        timeout_seconds=timeout_seconds,
        environment=environment,
    )
    return _capture_successful_output(output_directory, stage, completed)


def _base_evidence(
    environment: Mapping[str, str],
    *,
    target: str,
    mode: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "started",
        "mode": mode,
        "target": target,
        "generated_at_utc": _utc_now(),
        "github": {
            "repository": environment.get("GITHUB_REPOSITORY", ""),
            "ref": environment.get("GITHUB_REF", ""),
            "commit_sha": environment.get("GITHUB_SHA", ""),
            "run_id": environment.get("GITHUB_RUN_ID", ""),
            "run_attempt": environment.get("GITHUB_RUN_ATTEMPT", ""),
            "workflow": environment.get("GITHUB_WORKFLOW", ""),
        },
        "authentication": {
            "auth_type": environment.get("DATABRICKS_AUTH_TYPE", ""),
            "host_fingerprint": _fingerprint(environment.get("DATABRICKS_HOST")),
            "configured_client_id_fingerprint": _fingerprint(
                environment.get("DATABRICKS_CLIENT_ID")
            ),
        },
    }


def _record_failure(evidence: dict[str, Any], error: EvidenceError) -> None:
    failure: dict[str, Any] = {
        "stage": error.stage,
        "category": error.category,
    }
    if error.exit_code is not None:
        failure["exit_code"] = error.exit_code
    if error.stdout:
        failure["stdout"] = _text_metadata(error.stdout)
    if error.stderr:
        failure["stderr"] = _text_metadata(error.stderr)
    evidence["status"] = "failed"
    evidence["failure"] = failure
    evidence["completed_at_utc"] = _utc_now()


def render_summary(evidence: Mapping[str, Any]) -> str:
    """Render a bounded GitHub step summary without raw workspace identifiers."""

    github = evidence.get("github", {})
    authentication = evidence.get("authentication", {})
    lines = [
        "# Databricks plan evidence",
        "",
        f"- Status: **{evidence.get('status', 'unknown')}**",
        f"- Mode: `{evidence.get('mode', '')}`",
        f"- Target: `{evidence.get('target', '')}`",
        f"- Repository: `{github.get('repository', '')}`",
        f"- Commit: `{github.get('commit_sha', '')}`",
        f"- Workflow run: `{github.get('run_id', '')}` attempt `{github.get('run_attempt', '')}`",
        f"- Authentication: `{authentication.get('auth_type', '')}`",
    ]
    identity = evidence.get("identity")
    if isinstance(identity, dict) and identity.get("application_id_fingerprint"):
        lines.append(
            "- Authenticated application fingerprint: "
            f"`{identity['application_id_fingerprint']}`"
        )
    for stage in ("validation", "plan"):
        stage_result = evidence.get(stage)
        if isinstance(stage_result, dict):
            lines.append(
                f"- {stage.title()}: **{stage_result.get('status', 'unknown')}**"
            )
            if stage_result.get("output_file"):
                lines.append(
                    f"  - Output: `{stage_result['output_file']}` "
                    f"(`{stage_result.get('output_sha256', '')}`)"
                )
    failure = evidence.get("failure")
    if isinstance(failure, dict):
        lines.extend(
            [
                f"- Failure stage: `{failure.get('stage', '')}`",
                f"- Failure category: `{failure.get('category', '')}`",
            ]
        )
    lines.extend(
        [
            "",
            "The evidence contains fingerprints and output hashes, not credentials or raw identity values.",
            "A successful plan is review evidence only and does not deploy or mutate Databricks state.",
            "",
        ]
    )
    return "\n".join(lines)


def capture_evidence(
    *,
    target: str,
    mode: str,
    output_directory: Path,
    bundle_variables: Sequence[str],
    environment: Mapping[str, str],
    identity_timeout_seconds: float,
    validate_timeout_seconds: float,
    plan_timeout_seconds: float,
) -> int:
    evidence = _base_evidence(environment, target=target, mode=mode)
    prepared_directory: Path | None = None
    try:
        prepared_directory = _prepare_output_directory(output_directory)
        normalized_variables = normalize_bundle_variables(bundle_variables)
        validate_environment(environment, target)
        evidence["identity"] = verify_identity(
            environment,
            timeout_seconds=identity_timeout_seconds,
        )
        if mode == "plan":
            evidence["validation"] = capture_bundle_stage(
                "validate",
                target,
                normalized_variables,
                prepared_directory,
                environment,
                timeout_seconds=validate_timeout_seconds,
            )
            evidence["plan"] = capture_bundle_stage(
                "plan",
                target,
                normalized_variables,
                prepared_directory,
                environment,
                timeout_seconds=plan_timeout_seconds,
            )
        evidence["status"] = "succeeded"
        evidence["completed_at_utc"] = _utc_now()
    except EvidenceError as error:
        _record_failure(evidence, error)
    except Exception:
        _record_failure(evidence, EvidenceError("internal", "unexpected_internal_error"))

    if prepared_directory is None:
        try:
            prepared_directory = _prepare_output_directory(output_directory)
        except EvidenceError:
            print("Databricks evidence failed: output_directory_unavailable", file=sys.stderr)
            return 1

    _write_json_atomic(prepared_directory / "evidence.json", evidence)
    _write_text_atomic(prepared_directory / "summary.md", render_summary(evidence))

    if evidence["status"] != "succeeded":
        failure = evidence.get("failure", {})
        print(
            "Databricks evidence failed during "
            f"{failure.get('stage', 'unknown')}: {failure.get('category', 'unknown')}",
            file=sys.stderr,
        )
        return 1

    print(f"Databricks {mode} evidence captured for target {target}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(_ALLOWED_TARGETS))
    parser.add_argument("--mode", choices=("identity", "plan"), default="plan")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bundle-var", action="append", default=[])
    parser.add_argument(
        "--identity-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_IDENTITY_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--validate-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_VALIDATE_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--plan-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_PLAN_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return capture_evidence(
        target=args.target,
        mode=args.mode,
        output_directory=args.output_dir,
        bundle_variables=args.bundle_var,
        environment=os.environ,
        identity_timeout_seconds=args.identity_timeout_seconds,
        validate_timeout_seconds=args.validate_timeout_seconds,
        plan_timeout_seconds=args.plan_timeout_seconds,
    )


if __name__ == "__main__":
    sys.exit(main())
