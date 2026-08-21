"""Natural-identity reconciliation for warehouse publication.

The base warehouse audit checks counts, fact grain, null keys, and foreign-key
membership. This module independently reconstructs natural business identities
from fact surrogate keys and verifies that those identities match the Gold
source rows before any warehouse table is published.
"""

from __future__ import annotations

from typing import Mapping

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from lakehouse_demo.spark_warehouse import (
    FAILURE_FACT,
    UPTIME_FACT,
    WarehouseFinding,
    audit_warehouse,
)


_UPTIME_IDENTITY = (
    "event_date",
    "machine_id",
    "client_id",
    "site_id",
    "model",
)
_FAILURE_IDENTITY = (
    "event_id",
    "event_date",
    "machine_id",
    "client_id",
    "site_id",
    "model",
    "fault_code",
    "severity",
)
_REQUIRED_FRAMES = {
    "dim_client",
    "dim_fault",
    "dim_machine",
    "dim_model",
    "dim_site",
    UPTIME_FACT,
    FAILURE_FACT,
}


def _require_columns(dataframe: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _require_frames(warehouse_frames: Mapping[str, DataFrame]) -> None:
    missing = sorted(_REQUIRED_FRAMES.difference(warehouse_frames))
    if missing:
        raise ValueError(
            "warehouse frames are missing identity-audit datasets: "
            + ", ".join(missing)
        )


def _uptime_fact_identity(
    warehouse_frames: Mapping[str, DataFrame],
) -> DataFrame:
    fact = warehouse_frames[UPTIME_FACT]
    _require_columns(
        fact,
        {
            "event_date",
            "machine_key",
            "client_key",
            "site_key",
            "model_key",
        },
        label=UPTIME_FACT,
    )

    return (
        fact.join(
            warehouse_frames["dim_machine"].select(
                "machine_key",
                F.col("machine_id").alias("_identity_machine_id"),
            ),
            "machine_key",
            "left",
        )
        .join(
            warehouse_frames["dim_client"].select(
                "client_key",
                F.col("client_id").alias("_identity_client_id"),
            ),
            "client_key",
            "left",
        )
        .join(
            warehouse_frames["dim_site"].select(
                "site_key",
                F.col("site_id").alias("_identity_site_id"),
            ),
            "site_key",
            "left",
        )
        .join(
            warehouse_frames["dim_model"].select(
                "model_key",
                F.col("model").alias("_identity_model"),
            ),
            "model_key",
            "left",
        )
        .select(
            "event_date",
            F.col("_identity_machine_id").alias("machine_id"),
            F.col("_identity_client_id").alias("client_id"),
            F.col("_identity_site_id").alias("site_id"),
            F.col("_identity_model").alias("model"),
        )
    )


def _failure_fact_identity(
    warehouse_frames: Mapping[str, DataFrame],
) -> DataFrame:
    fact = warehouse_frames[FAILURE_FACT]
    _require_columns(
        fact,
        {
            "event_id",
            "event_date",
            "machine_key",
            "client_key",
            "site_key",
            "model_key",
            "fault_key",
        },
        label=FAILURE_FACT,
    )

    return (
        fact.join(
            warehouse_frames["dim_machine"].select(
                "machine_key",
                F.col("machine_id").alias("_identity_machine_id"),
            ),
            "machine_key",
            "left",
        )
        .join(
            warehouse_frames["dim_client"].select(
                "client_key",
                F.col("client_id").alias("_identity_client_id"),
            ),
            "client_key",
            "left",
        )
        .join(
            warehouse_frames["dim_site"].select(
                "site_key",
                F.col("site_id").alias("_identity_site_id"),
            ),
            "site_key",
            "left",
        )
        .join(
            warehouse_frames["dim_model"].select(
                "model_key",
                F.col("model").alias("_identity_model"),
            ),
            "model_key",
            "left",
        )
        .join(
            warehouse_frames["dim_fault"].select(
                "fault_key",
                F.col("fault_code").alias("_identity_fault_code"),
                F.col("severity").alias("_identity_severity"),
            ),
            "fault_key",
            "left",
        )
        .select(
            "event_id",
            "event_date",
            F.col("_identity_machine_id").alias("machine_id"),
            F.col("_identity_client_id").alias("client_id"),
            F.col("_identity_site_id").alias("site_id"),
            F.col("_identity_model").alias("model"),
            F.col("_identity_fault_code").alias("fault_code"),
            F.col("_identity_severity").alias("severity"),
        )
    )


def _identity_findings(
    *,
    source: DataFrame,
    fact_identity: DataFrame,
    identity_columns: tuple[str, ...],
    dataset: str,
) -> tuple[WarehouseFinding, ...]:
    _require_columns(source, set(identity_columns), label=f"{dataset} source")
    _require_columns(
        fact_identity,
        set(identity_columns),
        label=f"{dataset} reconstructed identity",
    )

    source_identities = source.select(*identity_columns).distinct()
    fact_identities = fact_identity.select(*identity_columns).distinct()

    missing = source_identities.join(
        fact_identities,
        list(identity_columns),
        "left_anti",
    ).count()
    unexpected = fact_identities.join(
        source_identities,
        list(identity_columns),
        "left_anti",
    ).count()

    findings: list[WarehouseFinding] = []
    if missing:
        findings.append(
            WarehouseFinding(
                code="missing_fact_identity",
                dataset=dataset,
                count=int(missing),
            )
        )
    if unexpected:
        findings.append(
            WarehouseFinding(
                code="unexpected_fact_identity",
                dataset=dataset,
                count=int(unexpected),
            )
        )
    return tuple(findings)


def audit_warehouse_identity(
    *,
    gold_uptime: DataFrame,
    gold_failures: DataFrame,
    warehouse_frames: Mapping[str, DataFrame],
) -> tuple[WarehouseFinding, ...]:
    """Compare Gold natural identities with identities reconstructed from facts."""

    _require_frames(warehouse_frames)

    findings = [
        *_identity_findings(
            source=gold_uptime,
            fact_identity=_uptime_fact_identity(warehouse_frames),
            identity_columns=_UPTIME_IDENTITY,
            dataset=UPTIME_FACT,
        ),
        *_identity_findings(
            source=gold_failures,
            fact_identity=_failure_fact_identity(warehouse_frames),
            identity_columns=_FAILURE_IDENTITY,
            dataset=FAILURE_FACT,
        ),
    ]
    return tuple(sorted(findings))


def audit_warehouse_publication(
    *,
    gold_uptime: DataFrame,
    gold_failures: DataFrame,
    warehouse_frames: Mapping[str, DataFrame],
) -> tuple[WarehouseFinding, ...]:
    """Run aggregate, referential, and natural-identity publication audits."""

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
    ]
    return tuple(sorted(findings))
