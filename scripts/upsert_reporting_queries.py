#!/usr/bin/env python3
"""Create or update governed Databricks SQL query assets."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "sql" / "reporting_assets"
MANIFEST = ASSET_DIR / "manifest.json"
POLICY_PATH = REPO_ROOT / "governance" / "reporting_query_policy.json"

DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0
QUERY_PAGE_SIZE = 100
MAX_QUERY_PAGES = 100
MAX_REPORTING_ASSETS = 50
MAX_ASSET_BYTES = 100_000
_REQUIRED_ASSET_KEYS = {"display_name", "description", "file"}
_QUERY_UPDATE_MASK = (
    "display_name,description,parent_path,query_text,warehouse_id,catalog,"
    "schema,run_as_mode,apply_auto_limit,tags"
)
_POLICY_KEYS = {
    "schema_version",
    "source_of_truth",
    "execution_mode",
    "workspace_editing",
    "owner_credentials_used_for_execution",
    "ownership_transfer",
    "legacy_owner_run_migration",
    "permissions",
    "verification",
}
_PERMISSION_KEYS = {
    "admin_group",
    "engineer_group",
    "analyst_group",
    "publisher_service_principal",
}
_VERIFICATION_KEYS = {
    "publisher_identity",
    "query_definition",
    "explicit_permissions",
    "reject_unexpected_groups",
    "reject_unexpected_service_principals",
}
_SAFE_APPLICATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_QUERY_TAGS = ("lakehouse-demo", "repository-managed", "viewer-run")


@dataclass(frozen=True)
class ReportingQueryPolicy:
    execution_mode: str
    admin_permission: str
    engineer_permission: str
    analyst_permission: str
    publisher_permission: str


def positive_seconds(value: str) -> float:
    """Parse a finite positive command timeout."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return parsed


def _run_command(
    command: list[str], *, timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    """Run one Databricks command without exposing arguments or output on failure."""

    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"Databricks CLI command exceeded {timeout_seconds:g} seconds"
        ) from None
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Databricks CLI command failed with exit code {exc.returncode}"
        ) from None
    except OSError:
        raise RuntimeError("Databricks CLI command could not be started") from None


def run_json(command: list[str], *, timeout_seconds: float) -> Any:
    completed = _run_command(command, timeout_seconds=timeout_seconds)
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        raise RuntimeError("Databricks CLI returned invalid JSON") from None


def run(command: list[str], *, timeout_seconds: float) -> None:
    _run_command(command, timeout_seconds=timeout_seconds)


def flatten_items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    if set(value) != expected:
        raise RuntimeError(f"{label} has an invalid shape")


def load_policy(path: Path = POLICY_PATH) -> ReportingQueryPolicy:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Reporting query policy could not be loaded") from None
    if not isinstance(payload, dict):
        raise RuntimeError("Reporting query policy must be a JSON object")
    _require_exact_keys(payload, _POLICY_KEYS, label="Reporting query policy")

    permissions = payload["permissions"]
    verification = payload["verification"]
    if not isinstance(permissions, dict) or not isinstance(verification, dict):
        raise RuntimeError("Reporting query policy sections are invalid")
    _require_exact_keys(
        permissions,
        _PERMISSION_KEYS,
        label="Reporting query permission policy",
    )
    _require_exact_keys(
        verification,
        _VERIFICATION_KEYS,
        label="Reporting query verification policy",
    )

    expected_scalars = {
        "schema_version": 1,
        "source_of_truth": "repository",
        "execution_mode": "VIEWER",
        "workspace_editing": "admin_only",
        "owner_credentials_used_for_execution": False,
        "ownership_transfer": "manual_workspace_admin",
        "legacy_owner_run_migration": "set_viewer_before_permissions",
    }
    for key, expected in expected_scalars.items():
        if payload[key] != expected:
            raise RuntimeError(f"Reporting query policy has an invalid {key}")

    expected_permissions = {
        "admin_group": "CAN_MANAGE",
        "engineer_group": "CAN_RUN",
        "analyst_group": "CAN_RUN",
        "publisher_service_principal": "CAN_MANAGE",
    }
    if permissions != expected_permissions:
        raise RuntimeError("Reporting query permission policy is not least privilege")
    if any(value is not True for value in verification.values()):
        raise RuntimeError("Reporting query verification controls must all be enabled")

    return ReportingQueryPolicy(
        execution_mode="VIEWER",
        admin_permission="CAN_MANAGE",
        engineer_permission="CAN_RUN",
        analyst_permission="CAN_RUN",
        publisher_permission="CAN_MANAGE",
    )


def validate_application_id(value: str) -> str:
    application_id = (value or "").strip()
    if not _SAFE_APPLICATION_ID.fullmatch(application_id):
        raise RuntimeError("Publisher application ID is missing or invalid")
    return application_id


def verify_publisher_identity(
    target: str,
    expected_application_id: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> None:
    expected = validate_application_id(expected_application_id)
    payload = run_json(
        ["databricks", "current-user", "me", "-t", target, "-o", "json"],
        timeout_seconds=command_timeout_seconds,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Databricks publisher identity response is invalid")
    actual = payload.get("application_id") or payload.get("applicationId")
    if not isinstance(actual, str) or actual != expected:
        raise RuntimeError("Databricks publisher identity did not match policy")


def find_warehouse_id(
    target: str,
    warehouse_name: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> str:
    payload = run_json(
        ["databricks", "warehouses", "list", "-t", target, "-o", "json"],
        timeout_seconds=command_timeout_seconds,
    )
    matches = [
        str(warehouse["id"])
        for warehouse in flatten_items(payload, ("warehouses", "results"))
        if warehouse.get("name") == warehouse_name and warehouse.get("id")
    ]
    if len(matches) > 1:
        raise RuntimeError("Multiple SQL warehouses share the requested name")
    if not matches:
        raise RuntimeError(f"SQL warehouse not found: {warehouse_name}")
    return matches[0]


def list_queries(
    target: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Read a bounded, complete accessible-query inventory page by page."""

    queries: list[dict[str, Any]] = []
    next_page_token: str | None = None
    seen_tokens: set[str] = set()

    for _page_number in range(MAX_QUERY_PAGES):
        command = [
            "databricks",
            "queries",
            "list",
            "-t",
            target,
            "--page-size",
            str(QUERY_PAGE_SIZE),
            "-o",
            "json",
        ]
        if next_page_token is not None:
            command.extend(["--page-token", next_page_token])

        payload = run_json(command, timeout_seconds=command_timeout_seconds)
        queries.extend(flatten_items(payload, ("results", "queries")))

        candidate_token = (
            payload.get("next_page_token") if isinstance(payload, dict) else None
        )
        if candidate_token is None or candidate_token == "":
            return queries
        if not isinstance(candidate_token, str):
            raise RuntimeError("Query inventory returned an invalid pagination token")
        if candidate_token in seen_tokens:
            raise RuntimeError("Query inventory pagination repeated a page token")
        seen_tokens.add(candidate_token)
        next_page_token = candidate_token

    raise RuntimeError("Query inventory exceeded the bounded page limit")


def find_active_query(
    existing_queries: list[dict[str, Any]], display_name: str
) -> dict[str, Any] | None:
    matches = [
        query
        for query in existing_queries
        if query.get("display_name") == display_name
        and query.get("lifecycle_state", "ACTIVE") == "ACTIVE"
    ]
    if len(matches) > 1:
        raise RuntimeError("Multiple active queries share the requested display name")
    if not matches:
        return None
    if not matches[0].get("id"):
        raise RuntimeError("Active query did not include an ID")
    return matches[0]


def find_active_query_id(
    existing_queries: list[dict[str, Any]], display_name: str
) -> str | None:
    query = find_active_query(existing_queries, display_name)
    return None if query is None else str(query["id"])


def load_assets() -> tuple[dict[str, str], ...]:
    """Load bounded, path-safe manifest entries and their UTF-8 SQL text."""

    try:
        raw_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Reporting query manifest could not be loaded") from None

    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise RuntimeError("Reporting query manifest must be a non-empty list")
    if len(raw_manifest) > MAX_REPORTING_ASSETS:
        raise RuntimeError("Reporting query manifest exceeds the asset limit")

    assets: list[dict[str, str]] = []
    seen_files: set[str] = set()
    seen_display_names: set[str] = set()

    for raw_asset in raw_manifest:
        if not isinstance(raw_asset, dict) or set(raw_asset) != _REQUIRED_ASSET_KEYS:
            raise RuntimeError("Reporting query manifest has an invalid asset shape")
        if any(
            not isinstance(raw_asset[key], str) or not raw_asset[key].strip()
            for key in _REQUIRED_ASSET_KEYS
        ):
            raise RuntimeError("Reporting query manifest has a blank asset field")

        display_name = raw_asset["display_name"].strip()
        description = raw_asset["description"].strip()
        file_name = raw_asset["file"]
        if file_name != file_name.strip() or Path(file_name).name != file_name:
            raise RuntimeError("Reporting query manifest contains an unsafe file")
        if not file_name.endswith(".sql"):
            raise RuntimeError("Reporting query asset must use the .sql extension")
        if file_name in seen_files:
            raise RuntimeError("Reporting query manifest contains a duplicate file")
        if display_name in seen_display_names:
            raise RuntimeError("Reporting query manifest contains a duplicate display name")
        seen_files.add(file_name)
        seen_display_names.add(display_name)

        file_path = ASSET_DIR / file_name
        try:
            if file_path.is_symlink() or not file_path.is_file():
                raise RuntimeError("Reporting query asset must be a regular file")
            raw_query = file_path.read_bytes()
        except RuntimeError:
            raise
        except OSError:
            raise RuntimeError("Reporting query asset could not be read") from None
        if len(raw_query) > MAX_ASSET_BYTES:
            raise RuntimeError("Reporting query asset exceeds the size limit")
        try:
            query_text = raw_query.decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("Reporting query asset is not valid UTF-8") from None
        if not query_text.strip():
            raise RuntimeError("Reporting query asset must not be empty")

        assets.append(
            {
                "display_name": display_name,
                "description": description,
                "file": file_name,
                "query_text": query_text,
            }
        )

    return tuple(assets)


def query_payload(
    *,
    target: str,
    display_name: str,
    description: str,
    query_text: str,
    warehouse_id: str,
    catalog: str,
    schema: str,
    parent_path: str,
    policy: ReportingQueryPolicy,
) -> dict[str, Any]:
    return {
        "auto_resolve_display_name": False,
        "query": {
            "display_name": display_name,
            "description": description,
            "parent_path": parent_path,
            "query_text": query_text,
            "warehouse_id": warehouse_id,
            "catalog": catalog,
            "schema": schema,
            "run_as_mode": policy.execution_mode,
            "apply_auto_limit": True,
            "tags": [*_QUERY_TAGS, target],
        },
    }


def upsert_query(
    target: str,
    display_name: str,
    description: str,
    query_text: str,
    warehouse_id: str,
    catalog: str,
    schema: str,
    parent_path: str,
    existing_queries: list[dict[str, Any]],
    *,
    policy: ReportingQueryPolicy | None = None,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> str:
    active_policy = policy or load_policy()
    payload = query_payload(
        target=target,
        display_name=display_name,
        description=description,
        query_text=query_text,
        warehouse_id=warehouse_id,
        catalog=catalog,
        schema=schema,
        parent_path=parent_path,
        policy=active_policy,
    )

    query_id = find_active_query_id(existing_queries, display_name)
    if query_id is not None:
        run(
            [
                "databricks",
                "queries",
                "update",
                query_id,
                _QUERY_UPDATE_MASK,
                "-t",
                target,
                "--json",
                json.dumps(payload),
            ],
            timeout_seconds=command_timeout_seconds,
        )
        return query_id

    created = run_json(
        [
            "databricks",
            "queries",
            "create",
            "-t",
            target,
            "--json",
            json.dumps(payload),
            "-o",
            "json",
        ],
        timeout_seconds=command_timeout_seconds,
    )
    created_id = created.get("id") if isinstance(created, dict) else None
    if not created_id:
        raise RuntimeError("Query creation did not return an ID")
    return str(created_id)


def get_query(
    target: str,
    query_id: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = run_json(
        [
            "databricks",
            "queries",
            "get",
            query_id,
            "-t",
            target,
            "-o",
            "json",
        ],
        timeout_seconds=command_timeout_seconds,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Databricks query read-back response is invalid")
    document = payload.get("query", payload)
    if not isinstance(document, dict):
        raise RuntimeError("Databricks query read-back document is invalid")
    return document


def verify_query_definition(
    actual: Mapping[str, Any],
    *,
    target: str,
    display_name: str,
    description: str,
    query_text: str,
    warehouse_id: str,
    catalog: str,
    schema: str,
    parent_path: str,
    policy: ReportingQueryPolicy,
) -> None:
    expected = {
        "display_name": display_name,
        "description": description,
        "parent_path": parent_path,
        "query_text": query_text,
        "warehouse_id": warehouse_id,
        "catalog": catalog,
        "schema": schema,
        "run_as_mode": policy.execution_mode,
    }
    if any(actual.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Databricks query definition did not match repository state")
    tags = actual.get("tags")
    if not isinstance(tags, list) or not set([*_QUERY_TAGS, target]).issubset(
        {tag for tag in tags if isinstance(tag, str)}
    ):
        raise RuntimeError("Databricks query tags did not match repository policy")
    if actual.get("run_as_mode") != "VIEWER":
        raise RuntimeError("Databricks query retained owner-run execution")


def permission_payload(
    *,
    admin_group: str,
    engineer_group: str,
    analyst_group: str,
    service_principal: str,
    policy: ReportingQueryPolicy,
) -> dict[str, Any]:
    return {
        "access_control_list": [
            {
                "group_name": admin_group,
                "permission_level": policy.admin_permission,
            },
            {
                "group_name": engineer_group,
                "permission_level": policy.engineer_permission,
            },
            {
                "group_name": analyst_group,
                "permission_level": policy.analyst_permission,
            },
            {
                "service_principal_name": service_principal,
                "permission_level": policy.publisher_permission,
            },
        ]
    }


def apply_query_permissions(
    target: str,
    query_id: str,
    admin_group: str,
    engineer_group: str,
    analyst_group: str,
    service_principal: str,
    *,
    policy: ReportingQueryPolicy | None = None,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> None:
    active_policy = policy or load_policy()
    payload = permission_payload(
        admin_group=admin_group,
        engineer_group=engineer_group,
        analyst_group=analyst_group,
        service_principal=service_principal,
        policy=active_policy,
    )
    run(
        [
            "databricks",
            "permissions",
            "set",
            "queries",
            query_id,
            "-t",
            target,
            "--json",
            json.dumps(payload),
        ],
        timeout_seconds=command_timeout_seconds,
    )


def get_query_permissions(
    target: str,
    query_id: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = run_json(
        [
            "databricks",
            "permissions",
            "get",
            "queries",
            query_id,
            "-t",
            target,
            "-o",
            "json",
        ],
        timeout_seconds=command_timeout_seconds,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Databricks query permissions response is invalid")
    return payload


def _principal_key(entry: Mapping[str, Any]) -> tuple[str, str] | None:
    for principal_type in ("group_name", "service_principal_name", "user_name"):
        value = entry.get(principal_type)
        if isinstance(value, str) and value:
            return principal_type, value
    return None


def _permission_levels(entry: Mapping[str, Any]) -> list[tuple[str, bool]]:
    direct = entry.get("permission_level")
    if isinstance(direct, str):
        return [(direct, False)]
    raw_permissions = entry.get("all_permissions")
    if not isinstance(raw_permissions, list):
        return []
    levels: list[tuple[str, bool]] = []
    for permission in raw_permissions:
        if not isinstance(permission, dict):
            continue
        level = permission.get("permission_level")
        inherited = permission.get("inherited", False)
        if isinstance(level, str) and isinstance(inherited, bool):
            levels.append((level, inherited))
    return levels


def verify_query_permissions(
    payload: Mapping[str, Any],
    *,
    admin_group: str,
    engineer_group: str,
    analyst_group: str,
    service_principal: str,
    policy: ReportingQueryPolicy,
) -> None:
    raw_acl = payload.get("access_control_list")
    if not isinstance(raw_acl, list):
        raise RuntimeError("Databricks query permissions did not include an ACL")

    expected = {
        ("group_name", admin_group): policy.admin_permission,
        ("group_name", engineer_group): policy.engineer_permission,
        ("group_name", analyst_group): policy.analyst_permission,
        (
            "service_principal_name",
            service_principal,
        ): policy.publisher_permission,
    }
    actual: dict[tuple[str, str], list[tuple[str, bool]]] = {}
    for raw_entry in raw_acl:
        if not isinstance(raw_entry, dict):
            raise RuntimeError("Databricks query ACL contains an invalid entry")
        key = _principal_key(raw_entry)
        if key is None:
            raise RuntimeError("Databricks query ACL entry did not identify a principal")
        if key in actual:
            raise RuntimeError("Databricks query ACL contains a duplicate principal")
        levels = _permission_levels(raw_entry)
        if not levels:
            raise RuntimeError("Databricks query ACL entry did not include permissions")
        actual[key] = levels

    for key, expected_level in expected.items():
        levels = actual.get(key)
        if levels is None or expected_level not in {level for level, _ in levels}:
            raise RuntimeError("Databricks query ACL did not match repository policy")

    elevated = {"CAN_EDIT", "CAN_MANAGE"}
    for key in (
        ("group_name", engineer_group),
        ("group_name", analyst_group),
    ):
        if elevated.intersection({level for level, _ in actual[key]}):
            raise RuntimeError("Databricks query ACL granted elevated human access")

    expected_keys = set(expected)
    for key, levels in actual.items():
        if key in expected_keys or key[0] == "user_name":
            continue
        if key[0] in {"group_name", "service_principal_name"} and any(
            not inherited for _, inherited in levels
        ):
            raise RuntimeError("Databricks query ACL contains an unexpected principal")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--catalog", default="main")
    parser.add_argument("--schema", default="lakehouse_demo")
    parser.add_argument("--warehouse-name", required=True)
    parser.add_argument("--parent-path", required=True)
    parser.add_argument("--admin-group", required=True)
    parser.add_argument("--engineer-group", required=True)
    parser.add_argument("--analyst-group", required=True)
    parser.add_argument("--service-principal", required=True)
    parser.add_argument(
        "--publisher-application-id",
        default=os.environ.get("DATABRICKS_CLIENT_ID", ""),
    )
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument(
        "--command-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = load_policy(args.policy)
    verify_publisher_identity(
        args.target,
        args.publisher_application_id,
        command_timeout_seconds=args.command_timeout_seconds,
    )
    warehouse_id = find_warehouse_id(
        args.target,
        args.warehouse_name,
        command_timeout_seconds=args.command_timeout_seconds,
    )
    existing_queries = list_queries(
        args.target,
        command_timeout_seconds=args.command_timeout_seconds,
    )

    for asset in load_assets():
        query_text = asset["query_text"].replace(
            "main.lakehouse_demo", f"{args.catalog}.{args.schema}"
        )
        display_name = f"{args.target.upper()} {asset['display_name']}"
        query_id = upsert_query(
            target=args.target,
            display_name=display_name,
            description=asset["description"],
            query_text=query_text,
            warehouse_id=warehouse_id,
            catalog=args.catalog,
            schema=args.schema,
            parent_path=args.parent_path,
            existing_queries=existing_queries,
            policy=policy,
            command_timeout_seconds=args.command_timeout_seconds,
        )
        query = get_query(
            args.target,
            query_id,
            command_timeout_seconds=args.command_timeout_seconds,
        )
        verify_query_definition(
            query,
            target=args.target,
            display_name=display_name,
            description=asset["description"],
            query_text=query_text,
            warehouse_id=warehouse_id,
            catalog=args.catalog,
            schema=args.schema,
            parent_path=args.parent_path,
            policy=policy,
        )
        apply_query_permissions(
            target=args.target,
            query_id=query_id,
            admin_group=args.admin_group,
            engineer_group=args.engineer_group,
            analyst_group=args.analyst_group,
            service_principal=args.service_principal,
            policy=policy,
            command_timeout_seconds=args.command_timeout_seconds,
        )
        permissions = get_query_permissions(
            args.target,
            query_id,
            command_timeout_seconds=args.command_timeout_seconds,
        )
        verify_query_permissions(
            permissions,
            admin_group=args.admin_group,
            engineer_group=args.engineer_group,
            analyst_group=args.analyst_group,
            service_principal=args.service_principal,
            policy=policy,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
