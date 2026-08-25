#!/usr/bin/env python3
"""Verify effective Databricks federation without mutating account state.

The verifier reads the ignored deployment-federation and runtime-identity
configuration, uses an already authenticated account-admin Databricks CLI
profile, and performs read-only service-principal, federation-policy, and secret
inventory commands. Evidence contains fingerprints and stable drift categories,
not account IDs, application IDs, numeric IDs, policy IDs, secret IDs, or raw
provider diagnostics.
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
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


ISSUER = "https://token.actions.githubusercontent.com"
REQUIRED_ENVIRONMENTS = ("dev-plan", "prod-plan", "dev", "prod")
DEFAULT_TIMEOUT_SECONDS = 60.0
PAGE_SIZE = 100
MAX_PAGES = 100
MAX_FINDINGS = 100
FORBIDDEN_AUTH_ENVIRONMENT = ("DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NUMERIC_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")


class VerificationError(RuntimeError):
    """A bounded failure safe to persist without provider diagnostics."""

    def __init__(self, stage: str, category: str) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage = stage
        self.category = category


@dataclass(frozen=True)
class PrincipalExpectation:
    numeric_id: str
    application_id: str


@dataclass(frozen=True)
class EnvironmentExpectation:
    deployment: PrincipalExpectation
    runtime: PrincipalExpectation


@dataclass(frozen=True)
class VerificationConfig:
    repository: str
    account_host: str
    account_id: str
    audience: str
    environments: Mapping[str, EnvironmentExpectation]


class DatabricksInventory(Protocol):
    def get_service_principal(self, numeric_id: str) -> Mapping[str, Any]: ...

    def list_federation_policies(self, numeric_id: str) -> list[dict[str, Any]]: ...

    def list_service_principal_secrets(
        self, numeric_id: str
    ) -> list[dict[str, Any]]: ...


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
        raise ValueError(f"{label} must be an https account URL")
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
    deployment_config_path: str | Path,
    runtime_config_path: str | Path,
) -> VerificationConfig:
    """Load and cross-check the two independently maintained bootstrap files."""

    deployment = _load_json_file(
        deployment_config_path, label="deployment federation config"
    )
    expected_deployment_keys = {
        "repository",
        "account_host",
        "account_id",
        "audience",
        "principals",
    }
    if set(deployment) != expected_deployment_keys:
        raise ValueError("deployment federation config has an invalid top-level shape")

    repository = deployment["repository"]
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise ValueError("repository must use owner/name form")
    account_host = _validate_host(deployment["account_host"], label="account host")
    account_id = _safe_identifier(
        deployment["account_id"], label="account ID", maximum=256
    )
    audience = _safe_identifier(
        deployment["audience"], label="audience", maximum=512
    )

    raw_deployment_principals = deployment["principals"]
    if not isinstance(raw_deployment_principals, dict) or set(
        raw_deployment_principals
    ) != set(REQUIRED_ENVIRONMENTS):
        raise ValueError("deployment federation config must define four environments")

    runtime = _load_json_file(runtime_config_path, label="runtime identity config")
    expected_runtime_keys = {
        "repository",
        "account_host",
        "account_id",
        "audience",
        "environments",
    }
    if set(runtime) != expected_runtime_keys:
        raise ValueError("runtime identity config has an invalid top-level shape")
    if runtime["repository"] != repository:
        raise ValueError("bootstrap configs refer to different repositories")
    if (
        _validate_host(runtime["account_host"], label="runtime account host")
        != account_host
    ):
        raise ValueError("bootstrap configs refer to different account hosts")
    if (
        _safe_identifier(runtime["account_id"], label="runtime account ID")
        != account_id
    ):
        raise ValueError("bootstrap configs refer to different account IDs")
    if _safe_identifier(runtime["audience"], label="runtime audience") != audience:
        raise ValueError("bootstrap configs refer to different audiences")

    raw_runtime_environments = runtime["environments"]
    if not isinstance(raw_runtime_environments, dict) or set(
        raw_runtime_environments
    ) != set(REQUIRED_ENVIRONMENTS):
        raise ValueError("runtime identity config must define four environments")

    environments: dict[str, EnvironmentExpectation] = {}
    deployment_numeric_ids: set[str] = set()
    deployment_application_ids: set[str] = set()
    runtime_numeric_ids: set[str] = set()
    runtime_application_ids: set[str] = set()

    for environment in REQUIRED_ENVIRONMENTS:
        raw_deployment = raw_deployment_principals[environment]
        if not isinstance(raw_deployment, dict) or set(raw_deployment) != {
            "numeric_id",
            "application_id",
        }:
            raise ValueError(f"deployment principal {environment} has an invalid shape")
        deployment_numeric_id = _safe_identifier(
            raw_deployment["numeric_id"],
            label=f"{environment} deployment numeric ID",
            maximum=32,
        )
        if not _NUMERIC_ID_PATTERN.fullmatch(deployment_numeric_id):
            raise ValueError(f"{environment} deployment numeric ID is invalid")
        deployment_application_id = _safe_identifier(
            raw_deployment["application_id"],
            label=f"{environment} deployment application ID",
            maximum=256,
        )

        raw_runtime = raw_runtime_environments[environment]
        if not isinstance(raw_runtime, dict) or set(raw_runtime) != {
            "deployment_client_id",
            "runtime_client_id",
            "runtime_numeric_id",
        }:
            raise ValueError(f"runtime environment {environment} has an invalid shape")
        runtime_deployment_id = _safe_identifier(
            raw_runtime["deployment_client_id"],
            label=f"{environment} runtime-config deployment client ID",
            maximum=256,
        )
        if runtime_deployment_id != deployment_application_id:
            raise ValueError(
                f"{environment} deployment application ID differs between configs"
            )
        runtime_application_id = _safe_identifier(
            raw_runtime["runtime_client_id"],
            label=f"{environment} runtime application ID",
            maximum=256,
        )
        runtime_numeric_id = _safe_identifier(
            raw_runtime["runtime_numeric_id"],
            label=f"{environment} runtime numeric ID",
            maximum=32,
        )
        if not _NUMERIC_ID_PATTERN.fullmatch(runtime_numeric_id):
            raise ValueError(f"{environment} runtime numeric ID is invalid")
        if deployment_application_id == runtime_application_id:
            raise ValueError(
                f"{environment} deployment and runtime application IDs must be distinct"
            )
        if deployment_numeric_id == runtime_numeric_id:
            raise ValueError(
                f"{environment} deployment and runtime numeric IDs must be distinct"
            )

        deployment_numeric_ids.add(deployment_numeric_id)
        deployment_application_ids.add(deployment_application_id)
        runtime_numeric_ids.add(runtime_numeric_id)
        runtime_application_ids.add(runtime_application_id)
        environments[environment] = EnvironmentExpectation(
            deployment=PrincipalExpectation(
                numeric_id=deployment_numeric_id,
                application_id=deployment_application_id,
            ),
            runtime=PrincipalExpectation(
                numeric_id=runtime_numeric_id,
                application_id=runtime_application_id,
            ),
        )

    if deployment_numeric_ids & runtime_numeric_ids:
        raise ValueError(
            "deployment and runtime numeric identities overlap across environments"
        )
    if deployment_application_ids & runtime_application_ids:
        raise ValueError(
            "deployment and runtime application identities overlap across environments"
        )

    return VerificationConfig(
        repository=repository,
        account_host=account_host,
        account_id=account_id,
        audience=audience,
        environments=environments,
    )


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        raise VerificationError("configuration", "output_directory_is_not_regular")
    return path


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class DatabricksCli:
    """Bounded read-only Databricks account inventory client."""

    def __init__(
        self,
        config: VerificationConfig,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout must be finite and positive")
        for name in FORBIDDEN_AUTH_ENVIRONMENT:
            if os.environ.get(name, "").strip():
                raise ValueError(
                    "static Databricks credential environment is not allowed"
                )
        environment = dict(os.environ)
        for name in (
            "DATABRICKS_AUTH_TYPE",
            "DATABRICKS_CLIENT_ID",
            "DATABRICKS_CLIENT_SECRET",
            "DATABRICKS_TOKEN",
        ):
            environment.pop(name, None)
        environment["DATABRICKS_HOST"] = config.account_host
        environment["DATABRICKS_ACCOUNT_ID"] = config.account_id
        self._environment = environment
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def _json(self, command: Sequence[str]) -> Any:
        try:
            completed = self._runner(
                list(command),
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=self._environment,
            )
        except subprocess.TimeoutExpired:
            raise VerificationError("databricks", "command_timed_out") from None
        except subprocess.CalledProcessError:
            raise VerificationError("databricks", "command_failed") from None
        except OSError:
            raise VerificationError("databricks", "command_could_not_start") from None
        try:
            return json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            raise VerificationError(
                "databricks", "command_returned_invalid_json"
            ) from None

    def get_service_principal(self, numeric_id: str) -> Mapping[str, Any]:
        payload = self._json(
            [
                "databricks",
                "account",
                "service-principals",
                "get",
                numeric_id,
                "-o",
                "json",
            ]
        )
        if not isinstance(payload, dict):
            raise VerificationError(
                "databricks", "service_principal_shape_is_invalid"
            )
        return payload

    def _list_all(
        self,
        *,
        command_prefix: Sequence[str],
        collection_keys: Sequence[str],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_page_token: str | None = None
        seen_tokens: set[str] = set()
        for _page in range(MAX_PAGES):
            command = [*command_prefix, "--page-size", str(PAGE_SIZE)]
            if next_page_token is not None:
                command.extend(["--page-token", next_page_token])
            command.extend(["-o", "json"])
            payload = self._json(command)
            if isinstance(payload, list):
                page_items = [item for item in payload if isinstance(item, dict)]
                items.extend(page_items)
                return items
            if not isinstance(payload, dict):
                raise VerificationError("databricks", "inventory_shape_is_invalid")
            raw_items: Any = None
            for key in collection_keys:
                if isinstance(payload.get(key), list):
                    raw_items = payload[key]
                    break
            if raw_items is None:
                raise VerificationError(
                    "databricks", "inventory_collection_is_missing"
                )
            page_items = [item for item in raw_items if isinstance(item, dict)]
            items.extend(page_items)
            candidate_token = payload.get("next_page_token")
            if candidate_token in (None, ""):
                return items
            if not isinstance(candidate_token, str):
                raise VerificationError(
                    "databricks", "pagination_token_is_invalid"
                )
            if candidate_token in seen_tokens:
                raise VerificationError(
                    "databricks", "pagination_token_repeated"
                )
            seen_tokens.add(candidate_token)
            next_page_token = candidate_token
        raise VerificationError("databricks", "inventory_exceeded_page_limit")

    def list_federation_policies(self, numeric_id: str) -> list[dict[str, Any]]:
        return self._list_all(
            command_prefix=[
                "databricks",
                "account",
                "service-principal-federation-policy",
                "list",
                numeric_id,
            ],
            collection_keys=("policies", "items", "results"),
        )

    def list_service_principal_secrets(
        self, numeric_id: str
    ) -> list[dict[str, Any]]:
        return self._list_all(
            command_prefix=[
                "databricks",
                "account",
                "service-principal-secrets",
                "list",
                numeric_id,
            ],
            collection_keys=("secrets", "items", "results"),
        )


def _normalise_role(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _account_admin_role_present(payload: Mapping[str, Any]) -> bool:
    roles = payload.get("roles")
    if not isinstance(roles, list):
        return False
    for role in roles:
        if not isinstance(role, dict):
            continue
        for key in ("value", "display"):
            if _normalise_role(role.get(key)) == "account_admin":
                return True
    return False


def _application_id(payload: Mapping[str, Any]) -> str | None:
    for key in ("applicationId", "application_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _audiences(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()


def _policy_matches(
    policy: Mapping[str, Any],
    *,
    numeric_id: str,
    issuer: str,
    audience: str,
    subject: str,
) -> bool:
    oidc = policy.get("oidc_policy")
    if not isinstance(oidc, dict):
        return False
    service_principal_id = policy.get("service_principal_id")
    if service_principal_id is not None and str(service_principal_id) != numeric_id:
        return False
    if oidc.get("issuer") != issuer or oidc.get("subject") != subject:
        return False
    if _audiences(oidc.get("audiences")) != (audience,):
        return False
    if oidc.get("subject_claim") not in (None, "", "sub"):
        return False
    if oidc.get("jwks_uri") not in (None, ""):
        return False
    if oidc.get("jwks_json") not in (None, ""):
        return False
    return True


def _policy_subject(policy: Mapping[str, Any]) -> str | None:
    oidc = policy.get("oidc_policy")
    if not isinstance(oidc, dict):
        return None
    subject = oidc.get("subject")
    return subject if isinstance(subject, str) and subject else None


def _add_finding(findings: list[dict[str, str]], category: str, scope: str) -> None:
    if len(findings) >= MAX_FINDINGS:
        if not any(item["category"] == "findings_truncated" for item in findings):
            findings.append({"category": "findings_truncated", "scope": "global"})
        return
    findings.append({"category": category, "scope": scope})


def _principal_expectations(
    config: VerificationConfig,
) -> dict[str, dict[str, Any]]:
    principals: dict[str, dict[str, Any]] = {}
    for environment, expectation in config.environments.items():
        for role, principal in (
            ("deployment", expectation.deployment),
            ("runtime", expectation.runtime),
        ):
            existing = principals.setdefault(
                principal.numeric_id,
                {
                    "application_id": principal.application_id,
                    "expected_policies": [],
                    "roles": set(),
                    "environments": set(),
                },
            )
            if existing["application_id"] != principal.application_id:
                raise ValueError("one numeric ID maps to multiple application IDs")
            existing["roles"].add(role)
            existing["environments"].add(environment)
            existing["expected_policies"].append(
                {
                    "environment": environment,
                    "role": role,
                    "subject": f"repo:{config.repository}:environment:{environment}",
                }
            )
    return principals


def verify_state(
    config: VerificationConfig,
    *,
    inventory: DatabricksInventory,
) -> dict[str, Any]:
    """Compare effective principal, policy, and secret state with configuration."""

    findings: list[dict[str, str]] = []
    principal_results: list[dict[str, Any]] = []
    for numeric_id, expected in sorted(_principal_expectations(config).items()):
        application_id = str(expected["application_id"])
        roles = sorted(str(item) for item in expected["roles"])
        environments = sorted(str(item) for item in expected["environments"])
        scope = "+".join(roles) + ":" + ",".join(environments)

        principal = inventory.get_service_principal(numeric_id)
        id_matches = str(principal.get("id", "")) == numeric_id
        application_matches = _application_id(principal) == application_id
        active = principal.get("active") is True
        account_admin_absent = not _account_admin_role_present(principal)
        if not id_matches:
            _add_finding(findings, "service_principal_numeric_id_mismatch", scope)
        if not application_matches:
            _add_finding(findings, "service_principal_application_id_mismatch", scope)
        if not active:
            _add_finding(findings, "service_principal_is_inactive", scope)
        if not account_admin_absent:
            _add_finding(findings, "service_principal_is_account_admin", scope)

        secrets = inventory.list_service_principal_secrets(numeric_id)
        secrets_absent = not secrets
        if not secrets_absent:
            _add_finding(findings, "service_principal_has_oauth_secrets", scope)

        policies = inventory.list_federation_policies(numeric_id)
        expected_policies = list(expected["expected_policies"])
        expected_subjects = {str(item["subject"]) for item in expected_policies}
        policy_results: list[dict[str, Any]] = []
        matched_policy_indexes: set[int] = set()
        for expected_policy in expected_policies:
            subject = str(expected_policy["subject"])
            subject_indexes = [
                index
                for index, policy in enumerate(policies)
                if _policy_subject(policy) == subject
            ]
            exact_indexes = [
                index
                for index in subject_indexes
                if _policy_matches(
                    policies[index],
                    numeric_id=numeric_id,
                    issuer=ISSUER,
                    audience=config.audience,
                    subject=subject,
                )
            ]
            policy_scope = (
                f"{expected_policy['role']}:{expected_policy['environment']}"
            )
            exact = len(exact_indexes) == 1 and len(subject_indexes) == 1
            if not subject_indexes:
                _add_finding(findings, "federation_policy_missing", policy_scope)
            elif not exact_indexes:
                _add_finding(findings, "federation_policy_mismatch", policy_scope)
            elif len(subject_indexes) > 1 or len(exact_indexes) > 1:
                _add_finding(findings, "duplicate_federation_policy", policy_scope)
            matched_policy_indexes.update(exact_indexes)
            policy_results.append(
                {
                    "environment": expected_policy["environment"],
                    "role": expected_policy["role"],
                    "exact_policy": exact,
                }
            )

        for index, policy in enumerate(policies):
            if index in matched_policy_indexes:
                continue
            subject = _policy_subject(policy)
            if subject in expected_subjects:
                continue
            _add_finding(findings, "unexpected_federation_policy", scope)

        principal_results.append(
            {
                "numeric_id_fingerprint": _fingerprint(numeric_id),
                "application_id_fingerprint": _fingerprint(application_id),
                "roles": roles,
                "environments": environments,
                "numeric_id_matches": id_matches,
                "application_id_matches": application_matches,
                "active": active,
                "account_admin_absent": account_admin_absent,
                "oauth_secrets_absent": secrets_absent,
                "oauth_secret_count": len(secrets),
                "federation_policy_count": len(policies),
                "policies": policy_results,
            }
        )

    return {
        "schema_version": 1,
        "status": "verified" if not findings else "blocked",
        "generated_at_utc": _utc_now(),
        "repository": config.repository,
        "account_host_fingerprint": _fingerprint(config.account_host),
        "account_id_fingerprint": _fingerprint(config.account_id),
        "audience_fingerprint": _fingerprint(config.audience),
        "issuer": ISSUER,
        "principals": principal_results,
        "findings": findings,
    }


def render_summary(evidence: Mapping[str, Any]) -> str:
    findings = evidence.get("findings", [])
    principals = evidence.get("principals", [])
    lines = [
        "# Databricks federation verification",
        "",
        f"- Status: **{evidence.get('status', 'unknown')}**",
        f"- Repository: `{evidence.get('repository', '')}`",
        f"- Principals checked: `{len(principals) if isinstance(principals, list) else 'unknown'}`",
        f"- Findings: `{len(findings) if isinstance(findings, list) else 'unknown'}`",
    ]
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict):
                lines.append(
                    f"  - `{finding.get('category', '')}` in `{finding.get('scope', '')}`"
                )
    lines.extend(
        [
            "",
            "The verifier performs read-only Databricks account inventory commands.",
            "Evidence contains fingerprints, counts, booleans, roles, environments, and",
            "stable drift categories—not account IDs, application IDs, numeric IDs,",
            "policy IDs, secret IDs, credential values, or provider diagnostics.",
            "",
        ]
    )
    return "\n".join(lines)


def capture_verification(
    *,
    config: VerificationConfig,
    inventory: DatabricksInventory,
    output_directory: Path,
) -> int:
    prepared = _prepare_output_directory(output_directory)
    try:
        evidence = verify_state(config, inventory=inventory)
    except VerificationError as error:
        evidence = {
            "schema_version": 1,
            "status": "failed",
            "generated_at_utc": _utc_now(),
            "repository": config.repository,
            "failure": {"stage": error.stage, "category": error.category},
            "principals": [],
            "findings": [],
        }
    _write_json_atomic(
        prepared / "databricks-federation-verification.json", evidence
    )
    _write_text_atomic(
        prepared / "databricks-federation-verification.md",
        render_summary(evidence),
    )
    return 0 if evidence["status"] == "verified" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-config", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--timeout-seconds", type=positive_seconds, default=DEFAULT_TIMEOUT_SECONDS
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.deployment_config, args.runtime_config)
        inventory = DatabricksCli(config, timeout_seconds=args.timeout_seconds)
        return capture_verification(
            config=config,
            inventory=inventory,
            output_directory=args.output_dir,
        )
    except (ValueError, VerificationError) as error:
        category = (
            error.category
            if isinstance(error, VerificationError)
            else "configuration_is_invalid"
        )
        print(f"Databricks federation verification failed: {category}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
