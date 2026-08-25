"""Structured validation/plan capture and command-line entry point."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core import (
    ALLOWED_TARGETS, DEFAULT_IDENTITY_TIMEOUT_SECONDS, DEFAULT_PLAN_TIMEOUT_SECONDS,
    DEFAULT_VALIDATE_TIMEOUT_SECONDS, EvidenceError, MAX_CAPTURE_BYTES,
    PLAN_OUTPUT_FILE, VALIDATION_OUTPUT_FILE, fingerprint, normalize_bundle_variables,
    positive_seconds, prepare_output_directory, run_command, text_metadata,
    validate_environment, verify_identity, write_json_atomic, write_text_atomic,
)

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _bounded(stage: str, stdout: str, stderr: str) -> tuple[int, int]:
    stdout_bytes = len(stdout.encode("utf-8", errors="replace"))
    stderr_bytes = len(stderr.encode("utf-8", errors="replace"))
    if stdout_bytes + stderr_bytes > MAX_CAPTURE_BYTES:
        raise EvidenceError(stage, "successful_output_exceeded_limit",
                            stdout=stdout, stderr=stderr)
    return stdout_bytes, stderr_bytes

def _warnings(directory: Path, stage: str, stderr: str, result: dict[str, Any]) -> None:
    if not stderr:
        return
    name = f"bundle-{stage}-warnings.txt"
    write_text_atomic(directory / name, stderr)
    encoded = stderr.encode()
    result.update({"warnings_file": name, "warnings_bytes": len(encoded),
                   "warnings_sha256": hashlib.sha256(encoded).hexdigest()})

def capture_bundle_stage(stage: str, target: str, bundle_variables: Sequence[str],
                         output_directory: Path, environment: Mapping[str, str], *,
                         timeout_seconds: float) -> dict[str, Any]:
    if stage not in {"validate", "plan"}:
        raise EvidenceError("configuration", "unsupported_bundle_stage")
    command = ["databricks", "bundle", stage, "--target", target]
    if stage == "plan":
        command.extend(["--output", "json"])
    for variable in bundle_variables:
        command.extend(["--var", variable])
    completed = run_command(command, stage=stage, timeout_seconds=timeout_seconds,
                            environment=environment)
    stdout_bytes, _ = _bounded(stage, completed.stdout, completed.stderr)
    if stage == "plan":
        try:
            parsed = json.loads(completed.stdout)
        except json.JSONDecodeError:
            raise EvidenceError("plan", "invalid_json_response", stdout=completed.stdout,
                                stderr=completed.stderr) from None
        if not isinstance(parsed, dict):
            raise EvidenceError("plan", "unexpected_json_shape", stdout=completed.stdout,
                                stderr=completed.stderr)
        result: dict[str, Any] = {
            "status": "succeeded",
            "format": "json",
            "output_file": PLAN_OUTPUT_FILE,
            "output_bytes": stdout_bytes,
            "output_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "top_level_type": "object",
        }
    else:
        result = {
            "status": "succeeded",
            "format": "text",
            "output_file": VALIDATION_OUTPUT_FILE,
            "output_bytes": stdout_bytes,
            "output_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        }
    write_text_atomic(output_directory / result["output_file"], completed.stdout)
    _warnings(output_directory, stage, completed.stderr, result)
    return result

def _base(environment: Mapping[str, str], target: str, mode: str) -> dict[str, Any]:
    return {"schema_version": 2, "status": "started", "mode": mode,
            "target": target, "generated_at_utc": utc_now(),
            "github": {key: environment.get(env, "") for key, env in {
                "repository": "GITHUB_REPOSITORY", "ref": "GITHUB_REF",
                "commit_sha": "GITHUB_SHA", "run_id": "GITHUB_RUN_ID",
                "run_attempt": "GITHUB_RUN_ATTEMPT", "workflow": "GITHUB_WORKFLOW"}.items()},
            "authentication": {"auth_type": environment.get("DATABRICKS_AUTH_TYPE", ""),
                "host_fingerprint": fingerprint(environment.get("DATABRICKS_HOST")),
                "configured_client_id_fingerprint": fingerprint(
                    environment.get("DATABRICKS_CLIENT_ID"))}}

def _record_failure(evidence: dict[str, Any], error: EvidenceError) -> None:
    failure: dict[str, Any] = {"stage": error.stage, "category": error.category}
    if error.exit_code is not None:
        failure["exit_code"] = error.exit_code
    if error.stdout:
        failure["stdout"] = text_metadata(error.stdout)
    if error.stderr:
        failure["stderr"] = text_metadata(error.stderr)
    evidence.update({"status": "failed", "failure": failure,
                     "completed_at_utc": utc_now()})

def render_summary(evidence: Mapping[str, Any]) -> str:
    github, auth = evidence.get("github", {}), evidence.get("authentication", {})
    lines = ["# Databricks plan evidence", "",
             f"- Status: **{evidence.get('status', 'unknown')}**",
             f"- Mode: `{evidence.get('mode', '')}`",
             f"- Target: `{evidence.get('target', '')}`",
             f"- Repository: `{github.get('repository', '')}`",
             f"- Commit: `{github.get('commit_sha', '')}`",
             f"- Workflow run: `{github.get('run_id', '')}` attempt `{github.get('run_attempt', '')}`",
             f"- Authentication: `{auth.get('auth_type', '')}`"]
    identity = evidence.get("identity")
    if isinstance(identity, dict) and identity.get("application_id_fingerprint"):
        lines.append(f"- Authenticated application fingerprint: `{identity['application_id_fingerprint']}`")
    for stage in ("validation", "plan"):
        result = evidence.get(stage)
        if isinstance(result, dict):
            lines.append(f"- {stage.title()}: **{result.get('status', 'unknown')}**")
            if result.get("output_file"):
                lines.append(f"  - Output: `{result['output_file']}` ({result.get('format')}, `{result.get('output_sha256', '')}`)")
    if isinstance(evidence.get("failure"), dict):
        failure = evidence["failure"]
        lines.extend([f"- Failure stage: `{failure.get('stage', '')}`",
                      f"- Failure category: `{failure.get('category', '')}`"])
    lines.extend(["", "Evidence contains fingerprints and hashes, not credentials or raw identities.",
                  "The validated JSON plan is review evidence only and does not mutate Databricks state.", ""])
    return "\n".join(lines)

def capture_evidence(*, target: str, mode: str, output_directory: Path,
                     bundle_variables: Sequence[str], environment: Mapping[str, str],
                     identity_timeout_seconds: float, validate_timeout_seconds: float,
                     plan_timeout_seconds: float) -> int:
    evidence = _base(environment, target, mode)
    prepared: Path | None = None
    try:
        prepared = prepare_output_directory(output_directory)
        variables = normalize_bundle_variables(bundle_variables)
        validate_environment(environment, target)
        evidence["identity"] = verify_identity(environment,
                                                 timeout_seconds=identity_timeout_seconds)
        if mode == "plan":
            evidence["validation"] = capture_bundle_stage("validate", target, variables,
                prepared, environment, timeout_seconds=validate_timeout_seconds)
            evidence["plan"] = capture_bundle_stage("plan", target, variables,
                prepared, environment, timeout_seconds=plan_timeout_seconds)
        evidence.update({"status": "succeeded", "completed_at_utc": utc_now()})
    except EvidenceError as error:
        _record_failure(evidence, error)
    except Exception:
        _record_failure(evidence, EvidenceError("internal", "unexpected_internal_error"))
    if prepared is None:
        try:
            prepared = prepare_output_directory(output_directory)
        except EvidenceError:
            print("Databricks evidence failed: output_directory_unavailable", file=sys.stderr)
            return 1
    write_json_atomic(prepared / "evidence.json", evidence)
    write_text_atomic(prepared / "summary.md", render_summary(evidence))
    if evidence["status"] != "succeeded":
        failure = evidence.get("failure", {})
        print(f"Databricks evidence failed during {failure.get('stage', 'unknown')}: {failure.get('category', 'unknown')}", file=sys.stderr)
        return 1
    print(f"Databricks {mode} evidence captured for target {target}")
    return 0

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(ALLOWED_TARGETS))
    parser.add_argument("--mode", choices=("identity", "plan"), default="plan")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bundle-var", action="append", default=[])
    parser.add_argument("--identity-timeout-seconds", type=positive_seconds,
                        default=DEFAULT_IDENTITY_TIMEOUT_SECONDS)
    parser.add_argument("--validate-timeout-seconds", type=positive_seconds,
                        default=DEFAULT_VALIDATE_TIMEOUT_SECONDS)
    parser.add_argument("--plan-timeout-seconds", type=positive_seconds,
                        default=DEFAULT_PLAN_TIMEOUT_SECONDS)
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    return capture_evidence(target=args.target, mode=args.mode,
        output_directory=args.output_dir, bundle_variables=args.bundle_var,
        environment=os.environ, identity_timeout_seconds=args.identity_timeout_seconds,
        validate_timeout_seconds=args.validate_timeout_seconds,
        plan_timeout_seconds=args.plan_timeout_seconds)
