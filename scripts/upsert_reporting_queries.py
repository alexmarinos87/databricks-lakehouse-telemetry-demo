#!/usr/bin/env python3
"""Create or update Databricks SQL Queries from local SQL assets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "sql" / "reporting_assets"
MANIFEST = ASSET_DIR / "manifest.json"


def run_json(command: list[str]) -> Any:
    output = subprocess.check_output(command, text=True)
    return json.loads(output or "{}")


def run(command: list[str]) -> None:
    subprocess.check_call(command)


def flatten_items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def find_warehouse_id(target: str, warehouse_name: str) -> str:
    payload = run_json(["databricks", "warehouses", "list", "-t", target, "-o", "json"])
    for warehouse in flatten_items(payload, ("warehouses", "results")):
        if warehouse.get("name") == warehouse_name:
            warehouse_id = warehouse.get("id")
            if warehouse_id:
                return warehouse_id

    raise RuntimeError(f"SQL warehouse not found: {warehouse_name}")


def list_queries(target: str) -> list[dict[str, Any]]:
    payload = run_json(["databricks", "queries", "list", "-t", target, "--limit", "100", "-o", "json"])
    return flatten_items(payload, ("results", "queries"))


def apply_query_permissions(
    target: str,
    query_id: str,
    admin_group: str,
    engineer_group: str,
    analyst_group: str,
    service_principal: str,
) -> None:
    payload = {
        "access_control_list": [
            {"group_name": admin_group, "permission_level": "CAN_MANAGE"},
            {"group_name": engineer_group, "permission_level": "CAN_EDIT"},
            {"group_name": analyst_group, "permission_level": "CAN_RUN"},
            {"service_principal_name": service_principal, "permission_level": "CAN_MANAGE"},
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
        ]
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

    existing = next(
        (
            query
            for query in existing_queries
            if query.get("display_name") == display_name and query.get("lifecycle_state", "ACTIVE") == "ACTIVE"
        ),
        None,
    )

    if existing and existing.get("id"):
        query_id = existing["id"]
        run(
            [
                "databricks",
                "queries",
                "update",
                query_id,
                "display_name,description,parent_path,query_text,warehouse_id,catalog,schema,run_as_mode,apply_auto_limit,tags",
                "-t",
                target,
                "--json",
                json.dumps(payload),
            ]
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
        ]
    )
    query_id = created.get("id")
    if not query_id:
        raise RuntimeError(f"Query creation did not return an id for {display_name}")
    return query_id


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    warehouse_id = find_warehouse_id(args.target, args.warehouse_name)
    existing_queries = list_queries(args.target)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for asset in manifest:
        query_text = (ASSET_DIR / asset["file"]).read_text(encoding="utf-8")
        query_text = query_text.replace("main.lakehouse_demo", f"{args.catalog}.{args.schema}")
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
        )
        apply_query_permissions(
            target=args.target,
            query_id=query_id,
            admin_group=args.admin_group,
            engineer_group=args.engineer_group,
            analyst_group=args.analyst_group,
            service_principal=args.service_principal,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
