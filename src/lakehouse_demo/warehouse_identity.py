"""Natural-identity reconstruction and reconciliation for warehouse publication.

The base warehouse audit checks counts, fact grain, null keys, and foreign-key
membership. This module resolves fact surrogate keys through their dimensions
and verifies that the resulting business identities match the Gold source rows.
"""

from __future__ import annotations

from typing import Mapping

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from lakehouse_demo.spark_warehouse import (
    FAILURE_FACT,
    UPTIME_FACT,
    WarehouseFinding,
)


UPTIME_IDENTITY_COLUMNS = (
    "event_date",
    "machine_id",
    "client_id",
    "site_id",
    "model",
)
FAILURE_IDENTITY_COLUMNS = (
    "event_id",
    "event_date",
    "machine_id",
    "client_id",
    "site_id",
    "model",
    "fault_code",
    "severity",
)
UPTIME_FACT_RECONCILIATION_COLUMNS = (
    "event_date",
    "running_minutes",
    "idle_minutes",
    "maintenance_minutes",
    "downtime_minutes",
    "observed_minutes",
    "uptime_pct",
    "idle_pct",
    "downtime_pct",
    "downtime_load_pct",
    "downtime_exceeds_observed",
    "downtime_semantics_version",
    "maintenance_pct",
    "avg_health_score",
)
FAILURE_FACT_RECONCILIATION_COLUMNS = (
    "event_date",
    "event_ts_utc",
    "failure_event_count",
    "temperature_c",
    "vibration_mm_s",
    "downtime_minutes",
    "maintenance_cost_gbp",
    "part_code",
    "part_quantity",
)
_REQUIRED_FRAMES = {
    "dim_client",
    "dim_date",
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


def _validate_uptime_inputs(warehouse_frames: Mapping[str, DataFrame]) -> None:
    _require_frames(warehouse_frames)
    _require_columns(
        warehouse_frames[UPTIME_FACT],
        {
            "date_key",
            "machine_key",
            "client_key",
            "site_key",
            "model_key",
            *UPTIME_FACT_RECONCILIATION_COLUMNS,
        },
        label=UPTIME_FACT,
    )
    _require_columns(
        warehouse_frames["dim_date"],
        {"date_key", "date_day"},
        label="dim_date",
    )
    _require_columns(
        warehouse_frames["dim_machine"],
        {"machine_key", "machine_id"},
        label="dim_machine",
    )
    _require_columns(
        warehouse_frames["dim_client"],
        {"client_key", "client_id"},
        label="dim_client",
    )
    _require_columns(
        warehouse_frames["dim_site"],
        {"site_key", "site_id"},
        label="dim_site",
    )
    _require_columns(
        warehouse_frames["dim_model"],
        {"model_key", "model"},
        label="dim_model",
    )


def _validate_failure_inputs(warehouse_frames: Mapping[str, DataFrame]) -> None:
    _require_frames(warehouse_frames)
    _require_columns(
        warehouse_frames[FAILURE_FACT],
        {
            "event_id",
            "date_key",
            "machine_key",
            "client_key",
            "site_key",
            "model_key",
            "fault_key",
            *FAILURE_FACT_RECONCILIATION_COLUMNS,
        },
        label=FAILURE_FACT,
    )
    _require_columns(
        warehouse_frames["dim_date"],
        {"date_key", "date_day"},
        label="dim_date",
    )
    _require_columns(
        warehouse_frames["dim_machine"],
        {"machine_key", "machine_id"},
        label="dim_machine",
    )
    _require_columns(
        warehouse_frames["dim_client"],
        {"client_key", "client_id"},
        label="dim_client",
    )
    _require_columns(
        warehouse_frames["dim_site"],
        {"site_key", "site_id"},
        label="dim_site",
    )
    _require_columns(
        warehouse_frames["dim_model"],
        {"model_key", "model"},
        label="dim_model",
    )
    _require_columns(
        warehouse_frames["dim_fault"],
        {"fault_key", "fault_code", "severity"},
        label="dim_fault",
    )


def reconstruct_uptime_fact_business_rows(
    warehouse_frames: Mapping[str, DataFrame],
) -> DataFrame:
    """Resolve uptime surrogate keys and retain prefixed fact values for audits."""

    _validate_uptime_inputs(warehouse_frames)
    fact = warehouse_frames[UPTIME_FACT].alias("fact")
    joined = (
        fact.join(
            warehouse_frames["dim_date"].alias("date"),
            F.col("fact.date_key") == F.col("date.date_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_machine"].alias("machine"),
            F.col("fact.machine_key") == F.col("machine.machine_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_client"].alias("client"),
            F.col("fact.client_key") == F.col("client.client_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_site"].alias("site"),
            F.col("fact.site_key") == F.col("site.site_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_model"].alias("model"),
            F.col("fact.model_key") == F.col("model.model_key"),
            "left",
        )
    )

    return joined.select(
        F.col("date.date_day").alias("event_date"),
        F.col("machine.machine_id").alias("machine_id"),
        F.col("client.client_id").alias("client_id"),
        F.col("site.site_id").alias("site_id"),
        F.col("model.model").alias("model"),
        *[
            F.col(f"fact.{column_name}").alias(f"fact_{column_name}")
            for column_name in UPTIME_FACT_RECONCILIATION_COLUMNS
        ],
    )


def reconstruct_failure_fact_business_rows(
    warehouse_frames: Mapping[str, DataFrame],
) -> DataFrame:
    """Resolve failure surrogate keys and retain prefixed fact values for audits."""

    _validate_failure_inputs(warehouse_frames)
    fact = warehouse_frames[FAILURE_FACT].alias("fact")
    joined = (
        fact.join(
            warehouse_frames["dim_date"].alias("date"),
            F.col("fact.date_key") == F.col("date.date_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_machine"].alias("machine"),
            F.col("fact.machine_key") == F.col("machine.machine_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_client"].alias("client"),
            F.col("fact.client_key") == F.col("client.client_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_site"].alias("site"),
            F.col("fact.site_key") == F.col("site.site_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_model"].alias("model"),
            F.col("fact.model_key") == F.col("model.model_key"),
            "left",
        )
        .join(
            warehouse_frames["dim_fault"].alias("fault"),
            F.col("fact.fault_key") == F.col("fault.fault_key"),
            "left",
        )
    )

    return joined.select(
        F.col("fact.event_id").alias("event_id"),
        F.col("date.date_day").alias("event_date"),
        F.col("machine.machine_id").alias("machine_id"),
        F.col("client.client_id").alias("client_id"),
        F.col("site.site_id").alias("site_id"),
        F.col("model.model").alias("model"),
        F.col("fault.fault_code").alias("fault_code"),
        F.col("fault.severity").alias("severity"),
        *[
            F.col(f"fact.{column_name}").alias(f"fact_{column_name}")
            for column_name in FAILURE_FACT_RECONCILIATION_COLUMNS
        ],
    )


def _identity_findings(
    *,
    source: DataFrame,
    fact_business_rows: DataFrame,
    identity_columns: tuple[str, ...],
    dataset: str,
) -> tuple[WarehouseFinding, ...]:
    _require_columns(source, set(identity_columns), label=f"{dataset} source")
    _require_columns(
        fact_business_rows,
        set(identity_columns),
        label=f"{dataset} reconstructed identity",
    )

    source_identities = source.select(*identity_columns).distinct()
    fact_identities = fact_business_rows.select(*identity_columns).distinct()

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
    """Compare Gold identities with identities resolved through fact dimensions."""

    findings = [
        *_identity_findings(
            source=gold_uptime,
            fact_business_rows=reconstruct_uptime_fact_business_rows(
                warehouse_frames
            ),
            identity_columns=UPTIME_IDENTITY_COLUMNS,
            dataset=UPTIME_FACT,
        ),
        *_identity_findings(
            source=gold_failures,
            fact_business_rows=reconstruct_failure_fact_business_rows(
                warehouse_frames
            ),
            identity_columns=FAILURE_IDENTITY_COLUMNS,
            dataset=FAILURE_FACT,
        ),
    ]
    return tuple(sorted(findings))
