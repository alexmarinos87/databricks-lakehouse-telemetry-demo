"""Executable attributed-downtime semantic contract for Spark datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


SEMANTIC_VERSION = "attributed_incident_v1"
FORMULA_TOLERANCE_PERCENTAGE_POINTS = 0.01
STATUS_MINUTE_COLUMNS = (
    "running_minutes",
    "idle_minutes",
    "maintenance_minutes",
)
REQUIRED_COLUMNS = STATUS_MINUTE_COLUMNS + (
    "observed_minutes",
    "downtime_minutes",
)


@dataclass(frozen=True, order=True)
class DowntimeSemanticFinding:
    code: str
    observed_count: int
    detail: str


def _missing_columns(dataframe: DataFrame, columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(columns).difference(dataframe.columns)))


def with_downtime_semantics(dataframe: DataFrame) -> DataFrame:
    """Add canonical load, exceedance, and semantic-version evidence.

    Attributed downtime is independent of observation coverage and may exceed
    observed minutes. The derived load is therefore not bounded to 100.
    """

    missing = _missing_columns(dataframe, REQUIRED_COLUMNS)
    if missing:
        raise ValueError(
            "downtime semantic input is missing columns: " + ", ".join(missing)
        )

    observed = F.col("observed_minutes").cast("double")
    downtime = F.col("downtime_minutes").cast("double")
    load = (
        F.when(observed > F.lit(0.0), (downtime / observed) * F.lit(100.0))
        .when((observed == F.lit(0.0)) & (downtime == F.lit(0.0)), F.lit(0.0))
        .otherwise(F.lit(None).cast("double"))
    )
    return (
        dataframe.withColumn("downtime_load_pct", F.round(load, 2))
        .withColumn(
            "downtime_exceeds_observed",
            F.when(
                downtime.isNotNull() & observed.isNotNull(),
                downtime > observed,
            ).otherwise(F.lit(None).cast("boolean")),
        )
        .withColumn("downtime_semantics_version", F.lit(SEMANTIC_VERSION))
    )


def audit_downtime_semantics(dataframe: DataFrame) -> tuple[DowntimeSemanticFinding, ...]:
    """Return bounded count-only findings for the semantic contract."""

    missing = _missing_columns(dataframe, REQUIRED_COLUMNS)
    if missing:
        return (
            DowntimeSemanticFinding(
                code="missing_semantic_columns",
                observed_count=len(missing),
                detail="Dataset is missing downtime semantic columns",
            ),
        )

    findings: list[DowntimeSemanticFinding] = []
    non_negative_columns = STATUS_MINUTE_COLUMNS + (
        "observed_minutes",
        "downtime_minutes",
    )
    negative_predicate = F.lit(False)
    null_predicate = F.lit(False)
    for column in non_negative_columns:
        negative_predicate = negative_predicate | (F.col(column) < F.lit(0))
        null_predicate = null_predicate | F.col(column).isNull()

    null_count = int(dataframe.where(null_predicate).count())
    if null_count:
        findings.append(
            DowntimeSemanticFinding(
                code="required_semantic_value_missing",
                observed_count=null_count,
                detail="Required duration values are null",
            )
        )

    negative_count = int(dataframe.where(negative_predicate).count())
    if negative_count:
        findings.append(
            DowntimeSemanticFinding(
                code="negative_duration_value",
                observed_count=negative_count,
                detail="Duration values must be non-negative",
            )
        )

    status_total = F.lit(0.0)
    for column in STATUS_MINUTE_COLUMNS:
        status_total = status_total + F.coalesce(
            F.col(column).cast("double"), F.lit(0.0)
        )
    partition_count = int(
        dataframe.where(
            status_total
            > F.coalesce(F.col("observed_minutes").cast("double"), F.lit(0.0))
        ).count()
    )
    if partition_count:
        findings.append(
            DowntimeSemanticFinding(
                code="status_minutes_exceed_observed",
                observed_count=partition_count,
                detail="Observed status minutes exceed observation coverage",
            )
        )

    canonical = with_downtime_semantics(dataframe)
    if "downtime_pct" in canonical.columns:
        formula_mismatch = int(
            canonical.where(
                ~F.col("downtime_pct")
                .cast("double")
                .eqNullSafe(F.col("downtime_load_pct"))
                & ~(
                    F.col("downtime_pct").cast("double").isNotNull()
                    & F.col("downtime_load_pct").isNotNull()
                    & (
                        F.abs(
                            F.col("downtime_pct").cast("double")
                            - F.col("downtime_load_pct")
                        )
                        <= F.lit(FORMULA_TOLERANCE_PERCENTAGE_POINTS)
                    )
                )
            ).count()
        )
        if formula_mismatch:
            findings.append(
                DowntimeSemanticFinding(
                    code="legacy_downtime_pct_formula_mismatch",
                    observed_count=formula_mismatch,
                    detail="Legacy downtime_pct does not match attributed downtime load",
                )
            )

    # There is intentionally no finding for downtime_minutes > observed_minutes
    # or downtime_load_pct > 100: both are valid under attributed_incident_v1.
    return tuple(sorted(findings))
