#!/usr/bin/env python3
"""Apply Unity Catalog grants that depend on runtime-created tables."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any


REPORTING_TABLES = (
    "gold_machine_uptime",
    "gold_failure_events",
    "gold_maintenance_costs",
    "gold_parts_usage",
    "gold_client_asset_summary",
    "gold_downtime_forecast_validation",
    "gold_downtime_forecast",
    "dim_client",
    "dim_date",
    "dim_fault",
    "dim_machine",
    "dim_model",
    "dim_site",
    "fact_machine_failure_event",
    "fact_machine_uptime_daily",
    "quality_check_results",
    "quality_metric_history",
    "quality_expectation_silver_machine_events",
    "quality_expectation_gold_machine_uptime",
    "quality_expectation_downtime_forecast",
)

DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
_ACTIVE_STATEMENT_STATES = {"PENDING", "RUNNING"}


def sql_identifier(*parts: str) -> str:
    return ".".join(f"`{part.replace('`', '``')}`" for part in parts)


def sql_principal(principal: str) -> str:
    return f"`{principal.replace('`', '``')}`"


def positive_seconds(value: str) -> float:
    """Parse a finite positive timeout or polling interval."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return parsed


def run_json(command: list[str], *, timeout_seconds: float) -> Any:
    """Execute one bounded Databricks CLI command and parse its JSON response.

    The raised errors intentionally omit command arguments and subprocess output,
    because both may contain principals, SQL statements, workspace identifiers,
    or provider diagnostics that should not be copied into broad CI logs.
    """

    try:
        completed = subprocess.run(
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

    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        raise RuntimeError("Databricks CLI returned invalid JSON") from None


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
    for warehouse in flatten_items(payload, ("warehouses", "results")):
        if warehouse.get("name") == warehouse_name and warehouse.get("id"):
            return str(warehouse["id"])
    raise RuntimeError(f"SQL warehouse not found: {warehouse_name}")


def _cancel_statement(
    target: str,
    statement_id: str,
    *,
    command_timeout_seconds: float,
) -> None:
    """Best-effort cancellation after the local statement deadline expires."""

    try:
        run_json(
            [
                "databricks",
                "api",
                "post",
                f"/api/2.0/sql/statements/{statement_id}/cancel",
                "-t",
                target,
                "-o",
                "json",
            ],
            timeout_seconds=command_timeout_seconds,
        )
    except (RuntimeError, TimeoutError):
        # The original timeout remains the authoritative failure. A failed cancel
        # attempt must not hide it or create an unbounded retry loop.
        return


def execute_statement(
    target: str,
    warehouse_id: str,
    catalog: str,
    schema: str,
    statement: str,
    *,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    statement_timeout_seconds: float = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Execute one SQL statement with bounded CLI calls and overall polling."""

    for label, value in (
        ("command timeout", command_timeout_seconds),
        ("statement timeout", statement_timeout_seconds),
        ("poll interval", poll_interval_seconds),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be finite and positive")

    payload = {
        "warehouse_id": warehouse_id,
        "catalog": catalog,
        "schema": schema,
        "statement": statement,
        "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE",
    }
    started_at = monotonic()
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
        ],
        timeout_seconds=command_timeout_seconds,
    )
    statement_id = result.get("statement_id")
    state = result.get("status", {}).get("state")

    if state == "SUCCEEDED":
        return
    if not statement_id:
        raise RuntimeError(
            "Databricks SQL statement response did not include a statement ID"
        )

    deadline = started_at + statement_timeout_seconds
    while state in _ACTIVE_STATEMENT_STATES:
        remaining = deadline - monotonic()
        if remaining <= 0:
            _cancel_statement(
                target,
                str(statement_id),
                command_timeout_seconds=command_timeout_seconds,
            )
            raise TimeoutError(
                f"Databricks SQL statement exceeded {statement_timeout_seconds:g} seconds"
            )

        sleep(min(poll_interval_seconds, remaining))
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
            ],
            timeout_seconds=command_timeout_seconds,
        )
        state = result.get("status", {}).get("state")

    if state != "SUCCEEDED":
        raise RuntimeError(
            f"Databricks SQL statement finished in {state or 'UNKNOWN'} state"
        )


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
    parser.add_argument(
        "--command-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--statement-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=positive_seconds,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    warehouse_id = find_warehouse_id(
        args.target,
        args.warehouse_name,
        command_timeout_seconds=args.command_timeout_seconds,
    )
    for statement in build_grants(args):
        execute_statement(
            args.target,
            warehouse_id,
            args.catalog,
            args.schema,
            statement,
            command_timeout_seconds=args.command_timeout_seconds,
            statement_timeout_seconds=args.statement_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
