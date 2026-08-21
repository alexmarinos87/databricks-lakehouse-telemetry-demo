#!/usr/bin/env python3
"""Create or update bounded Databricks SQL query assets."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "sql" / "reporting_assets"
MANIFEST = ASSET_DIR / "manifest.json"

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


def find_active_query_id(
    existing_queries: list[dict[str, Any]], display_name: str
) -> str | None:
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
    query_id = matches[0].get("id")
    if not query_id:
        raise RuntimeError("Active query did not include an ID")
    return str(query_id)


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


def apply_query_permissions(
    target: str,
    query_id: str,
    admin_group: str,
    engineer_group: str,
    analyst_group: str,
    service_principal: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> None:
    payload = {
        "access_control_list": [
            {"group_name": admin_group, "permission_level": "CAN_MANAGE"},
            {"group_name": engineer_group, "permission_level": "CAN_EDIT"},
            {"group_name": analyst_group, "permission_level": "CAN_RUN"},
            {
                "service_principal_name": service_principal,
                "permission_level": "CAN_MANAGE",
            },
        ]
    }
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
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> str:
    payload = {
        "auto_resolve_display_name": False,
        "query": {
            "display_name": display_name,
            "description": description,
            "parent_path": parent_path,
            "query_text": query_text,
            "warehouse_id": warehouse_id,
            "catalog": catalog,
            "schema": schema,
            "run_as_mode": "OWNER",
            "apply_auto_limit": True,
            "tags": ["lakehouse-demo", target],
        },
    }

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
        "--command-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
        query_id = upsert_query(
            target=args.target,
            display_name=f"{args.target.upper()} {asset['display_name']}",
            description=asset["description"],
            query_text=query_text,
            warehouse_id=warehouse_id,
            catalog=args.catalog,
            schema=args.schema,
            parent_path=args.parent_path,
            existing_queries=existing_queries,
            command_timeout_seconds=args.command_timeout_seconds,
        )
        apply_query_permissions(
            target=args.target,
            query_id=query_id,
            admin_group=args.admin_group,
            engineer_group=args.engineer_group,
            analyst_group=args.analyst_group,
            service_principal=args.service_principal,
            command_timeout_seconds=args.command_timeout_seconds,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
