#!/usr/bin/env python3
"""Apply Unity Catalog grants that depend on runtime-created tables."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any


REPORTING_TABLES = (
    "gold_machine_uptime",
    "gold_failure_events",
    "gold_maintenance_costs",
    "gold_parts_usage",
    "gold_client_asset_summary",
    "gold_downtime_forecast_validation",
    "gold_downtime_forecast",
    "quality_check_results",
    "quality_metric_history",
    "quality_expectation_silver_machine_events",
    "quality_expectation_gold_machine_uptime",
    "quality_expectation_downtime_forecast",
)


def sql_identifier(*parts: str) -> str:
    return ".".join(f"`{part.replace('`', '``')}`" for part in parts)


def sql_principal(principal: str) -> str:
    return f"`{principal.replace('`', '``')}`"


def run_json(command: list[str]) -> Any:
    output = subprocess.check_output(command, text=True)
    return json.loads(output or "{}")


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
        if warehouse.get("name") == warehouse_name and warehouse.get("id"):
            return warehouse["id"]
    raise RuntimeError(f"SQL warehouse not found: {warehouse_name}")


def execute_statement(target: str, warehouse_id: str, catalog: str, schema: str, statement: str) -> None:
    payload = {
        "warehouse_id": warehouse_id,
        "catalog": catalog,
        "schema": schema,
        "statement": statement,
        "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE",
    }
    result = run_json(
        [
            "databricks",
            "api",
            "post",
            "/api/2.0/sql/statements",
            "-t",
            target,
            "--json",
            json.dumps(payload),
            "-o",
            "json",
        ]
    )
    statement_id = result.get("statement_id")
    state = result.get("status", {}).get("state")
    while statement_id and state in {"PENDING", "RUNNING"}:
        time.sleep(5)
        result = run_json(
            [
                "databricks",
                "api",
                "get",
                f"/api/2.0/sql/statements/{statement_id}",
                "-t",
                target,
                "-o",
                "json",
            ]
        )
        state = result.get("status", {}).get("state")

    if state != "SUCCEEDED":
        message = result.get("status", {}).get("error", {}).get("message", result)
        raise RuntimeError(f"Statement failed: {statement}\n{message}")


def build_grants(args: argparse.Namespace) -> list[str]:
    catalog = sql_identifier(args.catalog)
    schema = sql_identifier(args.catalog, args.schema)
    volume = sql_identifier(args.catalog, args.schema, args.volume)

    admin = sql_principal(args.admin_group)
    engineer = sql_principal(args.engineer_group)
    analyst = sql_principal(args.analyst_group)
    service_principal = sql_principal(args.service_principal)

    statements = [
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {admin}",
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {engineer}",
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {analyst}",
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {service_principal}",
        f"GRANT USE SCHEMA ON SCHEMA {schema} TO {admin}",
        f"GRANT USE SCHEMA ON SCHEMA {schema} TO {engineer}",
        f"GRANT USE SCHEMA ON SCHEMA {schema} TO {analyst}",
        f"GRANT USE SCHEMA ON SCHEMA {schema} TO {service_principal}",
        f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {volume} TO {admin}",
        f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {volume} TO {engineer}",
        f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {volume} TO {service_principal}",
    ]

    if args.include_table_grants:
        statements.extend(
            f"GRANT SELECT ON TABLE {sql_identifier(args.catalog, args.schema, table_name)} TO {analyst}"
            for table_name in REPORTING_TABLES
        )

    return statements


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--catalog", default="main")
    parser.add_argument("--schema", default="lakehouse_demo")
    parser.add_argument("--volume", default="lakehouse_demo_files")
    parser.add_argument("--warehouse-name", required=True)
    parser.add_argument("--admin-group", required=True)
    parser.add_argument("--engineer-group", required=True)
    parser.add_argument("--analyst-group", required=True)
    parser.add_argument("--service-principal", required=True)
    parser.add_argument("--include-table-grants", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    warehouse_id = find_warehouse_id(args.target, args.warehouse_name)
    for statement in build_grants(args):
        execute_statement(args.target, warehouse_id, args.catalog, args.schema, statement)
    return 0


if __name__ == "__main__":
    sys.exit(main())
