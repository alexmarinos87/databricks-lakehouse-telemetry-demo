"""Measure-level source-to-fact reconciliation for warehouse publication.

The audit joins Gold rows to fact rows through reconstructed natural identities,
then compares direct and derived fact values with exact null-safe equality. It
returns only bounded dataset/column/count findings and never emits row values.
"""

from __future__ import annotations

from typing import Mapping

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from lakehouse_demo.downtime_semantics import downtime_impact_ratio
from lakehouse_demo.spark_warehouse import (
    FAILURE_FACT,
    UPTIME_FACT,
    WarehouseFinding,
)
from lakehouse_demo.warehouse_identity import (
    FAILURE_IDENTITY_COLUMNS,
    UPTIME_IDENTITY_COLUMNS,
    reconstruct_failure_fact_business_rows,
    reconstruct_uptime_fact_business_rows,
)


_UPTIME_MEASURES = (
    ("event_date", "event_date", "fact_event_date"),
    ("running_minutes", "running_minutes", "fact_running_minutes"),
    ("idle_minutes", "idle_minutes", "fact_idle_minutes"),
    (
        "maintenance_minutes",
        "maintenance_minutes",
        "fact_maintenance_minutes",
    ),
    ("downtime_minutes", "downtime_minutes", "fact_downtime_minutes"),
    ("observed_minutes", "observed_minutes", "fact_observed_minutes"),
    ("uptime_pct", "uptime_pct", "fact_uptime_pct"),
    ("idle_pct", "idle_pct", "fact_idle_pct"),
    (
        "downtime_impact_ratio_pct",
        "downtime_impact_ratio_pct",
        "fact_downtime_impact_ratio_pct",
    ),
    (
        "maintenance_pct",
        "maintenance_pct",
        "fact_maintenance_pct",
    ),
    (
        "avg_health_score",
        "avg_health_score",
        "fact_avg_health_score",
    ),
)
_FAILURE_MEASURES = (
    ("event_date", "event_date", "fact_event_date"),
    ("event_ts_utc", "event_ts_utc", "fact_event_ts_utc"),
    (
        "failure_event_count",
        "failure_event_count",
        "fact_failure_event_count",
    ),
    ("temperature_c", "temperature_c", "fact_temperature_c"),
    (
        "vibration_mm_s",
        "vibration_mm_s",
        "fact_vibration_mm_s",
    ),
    (
        "downtime_minutes",
        "downtime_minutes",
        "fact_downtime_minutes",
    ),
    (
        "maintenance_cost_gbp",
        "maintenance_cost_gbp",
        "fact_maintenance_cost_gbp",
    ),
    ("part_code", "part_code", "fact_part_code"),
    ("part_quantity", "part_quantity", "fact_part_quantity"),
)


def _require_columns(dataframe: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _percentage(numerator: str, denominator: str):
    return F.when(
        F.col(denominator) > 0,
        F.round(F.col(numerator) / F.col(denominator) * 100, 2),
    ).otherwise(F.lit(None).cast("double"))


def _expected_uptime_measures(gold_uptime: DataFrame) -> DataFrame:
    required = {
        *UPTIME_IDENTITY_COLUMNS,
        "running_minutes",
        "idle_minutes",
        "maintenance_minutes",
        "downtime_minutes",
        "observed_minutes",
        "uptime_pct",
        "avg_health_score",
    }
    _require_columns(gold_uptime, required, label="gold uptime measures")

    expected = (
        gold_uptime.withColumn(
            "idle_pct",
            _percentage("idle_minutes", "observed_minutes"),
        )
        .withColumn(
            "downtime_impact_ratio_pct",
            downtime_impact_ratio(),
        )
        .withColumn(
            "maintenance_pct",
            _percentage("maintenance_minutes", "observed_minutes"),
        )
    )
    selected_columns = tuple(
        dict.fromkeys(
            (
                *UPTIME_IDENTITY_COLUMNS,
                *(source_column for _, source_column, _ in _UPTIME_MEASURES),
            )
        )
    )
    return expected.select(*selected_columns)


def _expected_failure_measures(gold_failures: DataFrame) -> DataFrame:
    required = {
        *FAILURE_IDENTITY_COLUMNS,
        "event_ts_utc",
        "temperature_c",
        "vibration_mm_s",
        "downtime_minutes",
        "maintenance_cost_gbp",
        "part_code",
        "part_quantity",
    }
    _require_columns(gold_failures, required, label="gold failure measures")

    expected = gold_failures.withColumn("failure_event_count", F.lit(1))
    selected_columns = tuple(
        dict.fromkeys(
            (
                *FAILURE_IDENTITY_COLUMNS,
                *(source_column for _, source_column, _ in _FAILURE_MEASURES),
            )
        )
    )
    return expected.select(*selected_columns)


def _measure_findings(
    *,
    source: DataFrame,
    fact_business_rows: DataFrame,
    identity_columns: tuple[str, ...],
    measures: tuple[tuple[str, str, str], ...],
    dataset: str,
) -> tuple[WarehouseFinding, ...]:
    source_measure_columns = {source_column for _, source_column, _ in measures}
    fact_measure_columns = {fact_column for _, _, fact_column in measures}
    _require_columns(
        source,
        set(identity_columns) | source_measure_columns,
        label=f"{dataset} expected measures",
    )
    _require_columns(
        fact_business_rows,
        set(identity_columns) | fact_measure_columns,
        label=f"{dataset} fact measures",
    )

    source_rows = source.alias("source")
    fact_rows = fact_business_rows.alias("fact")
    join_condition = F.lit(True)
    for column_name in identity_columns:
        join_condition = join_condition & F.col(
            f"source.{column_name}"
        ).eqNullSafe(F.col(f"fact.{column_name}"))

    matched = source_rows.join(fact_rows, join_condition, "inner")
    aggregations = []
    for measure_name, source_column, fact_column in measures:
        mismatch = ~F.col(f"source.{source_column}").eqNullSafe(
            F.col(f"fact.{fact_column}")
        )
        aggregations.append(
            F.coalesce(
                F.sum(F.when(mismatch, F.lit(1)).otherwise(F.lit(0))),
                F.lit(0),
            )
            .cast("long")
            .alias(measure_name)
        )

    counts = matched.agg(*aggregations).collect()[0].asDict()
    return tuple(
        sorted(
            WarehouseFinding(
                code="measure_mismatch",
                dataset=f"{dataset}.{measure_name}",
                count=int(count),
            )
            for measure_name, count in counts.items()
            if count
        )
    )


def audit_warehouse_measures(
    *,
    gold_uptime: DataFrame,
    gold_failures: DataFrame,
    warehouse_frames: Mapping[str, DataFrame],
) -> tuple[WarehouseFinding, ...]:
    """Compare matched Gold and fact values for both warehouse fact families."""

    findings = [
        *_measure_findings(
            source=_expected_uptime_measures(gold_uptime),
            fact_business_rows=reconstruct_uptime_fact_business_rows(
                warehouse_frames
            ),
            identity_columns=UPTIME_IDENTITY_COLUMNS,
            measures=_UPTIME_MEASURES,
            dataset=UPTIME_FACT,
        ),
        *_measure_findings(
            source=_expected_failure_measures(gold_failures),
            fact_business_rows=reconstruct_failure_fact_business_rows(
                warehouse_frames
            ),
            identity_columns=FAILURE_IDENTITY_COLUMNS,
            measures=_FAILURE_MEASURES,
            dataset=FAILURE_FACT,
        ),
    ]
    return tuple(sorted(findings))
