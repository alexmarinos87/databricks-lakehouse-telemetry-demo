"""Composite warehouse publication audit.

Publication is allowed only when aggregate, referential, natural-identity, and
measure-level checks all return no findings.
"""

from __future__ import annotations

from typing import Mapping

from pyspark.sql import DataFrame

from lakehouse_demo.spark_warehouse import WarehouseFinding, audit_warehouse
from lakehouse_demo.warehouse_identity import audit_warehouse_identity
from lakehouse_demo.warehouse_measures import audit_warehouse_measures


def audit_warehouse_publication(
    *,
    gold_uptime: DataFrame,
    gold_failures: DataFrame,
    warehouse_frames: Mapping[str, DataFrame],
) -> tuple[WarehouseFinding, ...]:
    """Run every warehouse publication audit and return ordered findings."""

    findings = [
        *audit_warehouse(
            gold_uptime=gold_uptime,
            gold_failures=gold_failures,
            warehouse_frames=warehouse_frames,
        ),
        *audit_warehouse_identity(
            gold_uptime=gold_uptime,
            gold_failures=gold_failures,
            warehouse_frames=warehouse_frames,
        ),
        *audit_warehouse_measures(
            gold_uptime=gold_uptime,
            gold_failures=gold_failures,
            warehouse_frames=warehouse_frames,
        ),
    ]
    return tuple(sorted(findings))
