"""Validation, command execution, and identity checks for plan evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_IDENTITY_TIMEOUT_SECONDS = 60.0
DEFAULT_VALIDATE_TIMEOUT_SECONDS = 180.0
DEFAULT_PLAN_TIMEOUT_SECONDS = 300.0
MAX_BUNDLE_VARIABLES = 32
MAX_BUNDLE_VARIABLE_BYTES = 2_048
MAX_CAPTURE_BYTES = 4_000_000
PLAN_OUTPUT_FILE = "bundle-plan.json"
VALIDATION_OUTPUT_FILE = "bundle-validate.txt"
ALLOWED_TARGETS = {"dev", "prod"}
BUNDLE_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REQUIRED_GITHUB_CONTEXT = (
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "GITHUB_REPOSITORY",
    "GITHUB_REF",
    "GITHUB_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
)


class EvidenceError(RuntimeError):
    """A bounded failure safe to persist without provider diagnostics."""

    def __init__(
        self,
        stage: str,
        category: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        review: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage, self.category = stage, category
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr
        self.review = dict(review) if review is not None else None


def positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return parsed


def fingerprint(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def text_metadata(value: str) -> dict[str, int | str]:
    encoded = value.encode("utf-8", errors="replace")
    return {"bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def prepare_output_directory(path: Path) -> Path:
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
    if len(values) > MAX_BUNDLE_VARIABLES:
        raise EvidenceError("configuration", "too_many_bundle_variables")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if len(value.encode()) > MAX_BUNDLE_VARIABLE_BYTES:
            raise EvidenceError("configuration", "bundle_variable_too_large")
        if any(ch in value for ch in ("\x00", "\n", "\r")):
            raise EvidenceError("configuration", "bundle_variable_contains_control_character")
        if "=" not in value:
            raise EvidenceError("configuration", "bundle_variable_missing_separator")
        name, variable_value = value.split("=", 1)
        if not BUNDLE_VARIABLE_NAME.fullmatch(name):
            raise EvidenceError("configuration", "bundle_variable_name_is_invalid")
        if name in seen:
            raise EvidenceError("configuration", "duplicate_bundle_variable")
        if not variable_value:
            raise EvidenceError("configuration", "bundle_variable_value_is_blank")
        seen.add(name)
        normalized.append(value)
    return tuple(normalized)


def validate_environment(environment: Mapping[str, str], target: str) -> None:
    if target not in ALLOWED_TARGETS:
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
    if any(not environment.get(name, "").strip() for name in REQUIRED_GITHUB_CONTEXT):
        raise EvidenceError("configuration", "github_oidc_context_is_incomplete")


def run_command(command: Sequence[str], *, stage: str, timeout_seconds: float,
                environment: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(list(command), check=False, capture_output=True,
                                   text=True, timeout=timeout_seconds, env=dict(environment))
    except subprocess.TimeoutExpired as exc:
        raise EvidenceError(stage, "command_timed_out",
                            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                            stderr=exc.stderr if isinstance(exc.stderr, str) else "") from None
    except OSError:
        raise EvidenceError(stage, "command_could_not_be_started") from None
    if completed.returncode:
        raise EvidenceError(stage, "command_failed", exit_code=completed.returncode,
                            stdout=completed.stdout, stderr=completed.stderr)
    return completed


def _first_string(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def verify_identity(environment: Mapping[str, str], *, timeout_seconds: float) -> dict[str, Any]:
    completed = run_command(["databricks", "current-user", "me", "-o", "json"],
                            stage="identity", timeout_seconds=timeout_seconds,
                            environment=environment)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        raise EvidenceError("identity", "invalid_json_response", stdout=completed.stdout,
                            stderr=completed.stderr) from None
    if not isinstance(payload, dict):
        raise EvidenceError("identity", "unexpected_response_shape")
    if payload.get("active", True) is False:
        raise EvidenceError("identity", "authenticated_principal_is_inactive")
    expected = environment["DATABRICKS_CLIENT_ID"].strip()
    application_id = _first_string(payload, "application_id", "applicationId")
    if application_id is None and _first_string(payload, "user_name", "userName") == expected:
        application_id = expected
    if application_id is None:
        raise EvidenceError("identity", "authenticated_principal_is_not_a_service_principal")
    if application_id != expected:
        raise EvidenceError("identity", "authenticated_identity_does_not_match_client_id")
    principal_id = _first_string(payload, "id")
    if principal_id is None:
        raise EvidenceError("identity", "authenticated_principal_id_is_missing")
    return {"status": "succeeded", "active": True,
            "application_id_fingerprint": fingerprint(application_id),
            "principal_id_fingerprint": fingerprint(principal_id)}
