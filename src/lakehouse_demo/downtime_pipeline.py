"""Propagate the attributed-downtime contract through Gold and warehouse outputs.

The lower-level medallion and warehouse builders remain responsible for their
existing aggregation and dimensional logic. This module is the governed wrapper
used by Databricks notebooks and executable Spark tests. It materializes the
approved downtime fields, validates them without treating values above 100% as
invalid, and exposes a count-only warehouse publication audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from lakehouse_demo.spark_downtime_semantics import (
    FORMULA_TOLERANCE_PERCENTAGE_POINTS,
    SEMANTIC_VERSION,
    audit_downtime_semantics,
    with_downtime_semantics,
)
from lakehouse_demo.spark_medallion import build_gold_frames
from lakehouse_demo.spark_warehouse import (
    UPTIME_FACT,
    WarehouseFinding,
    build_warehouse_frames,
)


GOLD_UPTIME = "gold_machine_uptime"
MATERIALIZED_SEMANTIC_COLUMNS = (
    "downtime_pct",
    "downtime_load_pct",
    "downtime_exceeds_observed",
    "downtime_semantics_version",
)
_MATERIALIZED_SEMANTIC_COLUMN_SET = set(MATERIALIZED_SEMANTIC_COLUMNS)


@dataclass(frozen=True, order=True)
class MaterializedDowntimeFinding:
    code: str
    observed_count: int
    detail: str


def _missing_columns(dataframe: DataFrame, columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(columns).difference(dataframe.columns)))


def _canonical_load_expression():
    observed = F.col("observed_minutes").cast("double")
    downtime = F.col("downtime_minutes").cast("double")
    return (
        F.when(observed > F.lit(0.0), F.round(downtime / observed * 100.0, 2))
        .when((observed == F.lit(0.0)) & (downtime == F.lit(0.0)), F.lit(0.0))
        .otherwise(F.lit(None).cast("double"))
    )


def materialized_downtime_findings(
    dataframe: DataFrame,
) -> tuple[MaterializedDowntimeFinding, ...]:
    """Validate persisted semantic fields without imposing an upper load bound."""

    required = {
        "running_minutes",
        "idle_minutes",
        "maintenance_minutes",
        "observed_minutes",
        "downtime_minutes",
        *MATERIALIZED_SEMANTIC_COLUMNS,
    }
    missing = _missing_columns(dataframe, tuple(required))
    if missing:
        return (
            MaterializedDowntimeFinding(
                code="missing_materialized_downtime_columns",
                observed_count=len(missing),
                detail="Dataset is missing materialized downtime semantic columns",
            ),
        )

    findings: list[MaterializedDowntimeFinding] = []
    findings.extend(
        MaterializedDowntimeFinding(
            code=finding.code,
            observed_count=finding.observed_count,
            detail=finding.detail,
        )
        for finding in audit_downtime_semantics(dataframe)
    )

    expected_load = _canonical_load_expression()
    expected_exceeds = F.when(
        F.col("downtime_minutes").isNotNull()
        & F.col("observed_minutes").isNotNull(),
        F.col("downtime_minutes") > F.col("observed_minutes"),
    ).otherwise(F.lit(None).cast("boolean"))

    load_mismatch = int(
        dataframe.where(
            ~F.col("downtime_load_pct").cast("double").eqNullSafe(expected_load)
            & ~(
                F.col("downtime_load_pct").cast("double").isNotNull()
                & expected_load.isNotNull()
                & (
                    F.abs(
                        F.col("downtime_load_pct").cast("double") - expected_load
                    )
                    <= F.lit(FORMULA_TOLERANCE_PERCENTAGE_POINTS)
                )
            )
        ).count()
    )
    if load_mismatch:
        findings.append(
            MaterializedDowntimeFinding(
                code="downtime_load_formula_mismatch",
                observed_count=load_mismatch,
                detail="Materialized downtime load does not match attributed downtime",
            )
        )

    alias_mismatch = int(
        dataframe.where(
            ~F.col("downtime_pct")
            .cast("double")
            .eqNullSafe(F.col("downtime_load_pct").cast("double"))
            & ~(
                F.col("downtime_pct").cast("double").isNotNull()
                & F.col("downtime_load_pct").cast("double").isNotNull()
                & (
                    F.abs(
                        F.col("downtime_pct").cast("double")
                        - F.col("downtime_load_pct").cast("double")
                    )
                    <= F.lit(FORMULA_TOLERANCE_PERCENTAGE_POINTS)
                )
            )
        ).count()
    )
    if alias_mismatch:
        findings.append(
            MaterializedDowntimeFinding(
                code="downtime_compatibility_alias_mismatch",
                observed_count=alias_mismatch,
                detail="Legacy downtime_pct differs from downtime_load_pct",
            )
        )

    exceeds_mismatch = int(
        dataframe.where(
            ~F.col("downtime_exceeds_observed")
            .cast("boolean")
            .eqNullSafe(expected_exceeds)
        ).count()
    )
    if exceeds_mismatch:
        findings.append(
            MaterializedDowntimeFinding(
                code="downtime_exceedance_flag_mismatch",
                observed_count=exceeds_mismatch,
                detail="Downtime exceedance flag does not match duration evidence",
            )
        )

    version_mismatch = int(
        dataframe.where(
            ~F.col("downtime_semantics_version").eqNullSafe(
                F.lit(SEMANTIC_VERSION)
            )
        ).count()
    )
    if version_mismatch:
        findings.append(
            MaterializedDowntimeFinding(
                code="downtime_semantics_version_mismatch",
                observed_count=version_mismatch,
                detail="Dataset does not use the accepted downtime semantic version",
            )
        )

    return tuple(sorted(set(findings)))


def _raise_findings(
    findings: Sequence[MaterializedDowntimeFinding], *, label: str
) -> None:
    if findings:
        summary = ",".join(
            f"{finding.code}:{finding.observed_count}" for finding in findings
        )
        raise ValueError(f"{label} failed downtime semantic validation: {summary}")


def _materialize_and_validate(dataframe: DataFrame, *, label: str) -> DataFrame:
    enriched = with_downtime_semantics(dataframe).withColumn(
        "downtime_pct", F.col("downtime_load_pct")
    )
    _raise_findings(materialized_downtime_findings(enriched), label=label)
    return enriched


def ensure_materialized_downtime_semantics(
    dataframe: DataFrame, *, label: str
) -> DataFrame:
    """Materialize an unversioned frame or validate a fully versioned frame.

    A partial semantic schema fails closed. This prevents a frame carrying only
    the legacy ``downtime_pct`` alias from being mistaken for governed output.
    """

    present = _MATERIALIZED_SEMANTIC_COLUMN_SET.intersection(dataframe.columns)
    if not present:
        return _materialize_and_validate(dataframe, label=label)
    if present != _MATERIALIZED_SEMANTIC_COLUMN_SET:
        missing = sorted(_MATERIALIZED_SEMANTIC_COLUMN_SET.difference(present))
        raise ValueError(
            f"{label} has a partial downtime semantic schema: {', '.join(missing)}"
        )
    _raise_findings(materialized_downtime_findings(dataframe), label=label)
    return dataframe


def build_governed_gold_frames(silver: DataFrame) -> Mapping[str, DataFrame]:
    """Build Gold outputs and materialize the approved downtime contract."""

    frames = dict(build_gold_frames(silver))
    if GOLD_UPTIME not in frames:
        raise ValueError("Gold transformation did not return gold_machine_uptime")
    frames[GOLD_UPTIME] = ensure_materialized_downtime_semantics(
        frames[GOLD_UPTIME], label=GOLD_UPTIME
    )
    return frames


def build_governed_warehouse_frames(
    gold_uptime: DataFrame,
    gold_failures: DataFrame,
) -> Mapping[str, DataFrame]:
    """Build warehouse outputs and retain the same semantic evidence in the fact."""

    governed_gold_uptime = ensure_materialized_downtime_semantics(
        gold_uptime, label=GOLD_UPTIME
    )
    frames = dict(build_warehouse_frames(governed_gold_uptime, gold_failures))
    if UPTIME_FACT not in frames:
        raise ValueError("Warehouse transformation did not return the uptime fact")
    # The lower-level warehouse builder intentionally retains only the legacy
    # alias. Canonicalize its internal output before it becomes governed state.
    frames[UPTIME_FACT] = _materialize_and_validate(
        frames[UPTIME_FACT], label=UPTIME_FACT
    )
    return frames


def audit_warehouse_downtime_semantics(
    *,
    gold_uptime: DataFrame,
    warehouse_frames: Mapping[str, DataFrame],
) -> tuple[WarehouseFinding, ...]:
    """Return count-only publication findings for Gold and warehouse semantics."""

    if UPTIME_FACT not in warehouse_frames:
        raise ValueError("warehouse frames are missing the uptime fact")
    findings: list[WarehouseFinding] = []
    for dataset, dataframe in (
        (GOLD_UPTIME, gold_uptime),
        (UPTIME_FACT, warehouse_frames[UPTIME_FACT]),
    ):
        present = _MATERIALIZED_SEMANTIC_COLUMN_SET.intersection(dataframe.columns)
        if not present:
            governed = _materialize_and_validate(dataframe, label=dataset)
            dataset_findings: Sequence[MaterializedDowntimeFinding] = (
                materialized_downtime_findings(governed)
            )
        elif present != _MATERIALIZED_SEMANTIC_COLUMN_SET:
            dataset_findings = (
                MaterializedDowntimeFinding(
                    code="missing_materialized_downtime_columns",
                    observed_count=len(
                        _MATERIALIZED_SEMANTIC_COLUMN_SET.difference(present)
                    ),
                    detail="Dataset has a partial downtime semantic schema",
                ),
            )
        else:
            dataset_findings = materialized_downtime_findings(dataframe)
        findings.extend(
            WarehouseFinding(
                code=finding.code,
                dataset=dataset,
                count=finding.observed_count,
            )
            for finding in dataset_findings
        )
    return tuple(sorted(findings))
