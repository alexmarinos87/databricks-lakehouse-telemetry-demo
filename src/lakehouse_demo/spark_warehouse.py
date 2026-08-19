"""Executable PySpark warehouse construction and reconciliation evidence.

The functions in this module operate on DataFrames only. They do not read from or
write to catalogs, tables, volumes, checkpoints, or external services. Databricks
notebooks can own persistence while local Spark tests execute the same modelling
and audit behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


UPTIME_FACT = "fact_machine_uptime_daily"
FAILURE_FACT = "fact_machine_failure_event"

_DIMENSION_KEYS = {
    "date_key": ("dim_date", "date_key"),
    "client_key": ("dim_client", "client_key"),
    "machine_key": ("dim_machine", "machine_key"),
    "model_key": ("dim_model", "model_key"),
    "site_key": ("dim_site", "site_key"),
    "fault_key": ("dim_fault", "fault_key"),
}


@dataclass(frozen=True, order=True)
class WarehouseFinding:
    """A deterministic aggregate finding from executable Spark warehouse data."""

    code: str
    dataset: str
    count: int


def _require_columns(dataframe: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _first_count(dataframe: DataFrame) -> int:
    return int(dataframe.limit(1).count())


def _ensure_non_empty(dataframe: DataFrame, *, label: str) -> None:
    if _first_count(dataframe) == 0:
        raise ValueError(f"{label} must contain at least one row")


def _machine_assignments(uptime: DataFrame, failures: DataFrame) -> DataFrame:
    assignments = (
        uptime.select("machine_id", "site_id", "client_id", "model")
        .unionByName(
            failures.select("machine_id", "site_id", "client_id", "model"),
            allowMissingColumns=False,
        )
        .distinct()
    )
    conflicts = assignments.groupBy("machine_id").count().where(F.col("count") > 1)
    if _first_count(conflicts):
        raise ValueError("gold inputs contain conflicting machine assignments")
    return assignments


def build_warehouse_frames(
    uptime: DataFrame, failures: DataFrame
) -> Mapping[str, DataFrame]:
    """Build deterministic warehouse dimensions and facts from Gold DataFrames."""

    _require_columns(
        uptime,
        {
            "event_date",
            "site_id",
            "client_id",
            "machine_id",
            "model",
            "running_minutes",
            "idle_minutes",
            "maintenance_minutes",
            "downtime_minutes",
            "observed_minutes",
            "uptime_pct",
            "avg_health_score",
        },
        label="gold uptime",
    )
    _require_columns(
        failures,
        {
            "event_id",
            "event_date",
            "event_ts_utc",
            "site_id",
            "client_id",
            "machine_id",
            "model",
            "fault_code",
            "severity",
            "temperature_c",
            "vibration_mm_s",
            "downtime_minutes",
            "maintenance_cost_gbp",
            "part_code",
            "part_quantity",
        },
        label="gold failures",
    )
    _ensure_non_empty(uptime, label="gold uptime")
    assignments = _machine_assignments(uptime, failures)

    event_dates = (
        uptime.select("event_date")
        .unionByName(failures.select("event_date"))
        .where(F.col("event_date").isNotNull())
    )
    dates = (
        event_dates.agg(
            F.min("event_date").alias("first_date"),
            F.max("event_date").alias("last_date"),
        )
        .select(F.explode(F.sequence("first_date", "last_date")).alias("date_day"))
        .withColumn("date_key", F.date_format("date_day", "yyyyMMdd").cast("int"))
        .withColumn("year", F.year("date_day"))
        .withColumn("quarter", F.quarter("date_day"))
        .withColumn("month", F.month("date_day"))
        .withColumn("month_name", F.date_format("date_day", "MMMM"))
        .withColumn("year_month_key", F.date_format("date_day", "yyyyMM").cast("int"))
        .withColumn("year_month", F.date_format("date_day", "yyyy-MM"))
        .withColumn("day_of_month", F.dayofmonth("date_day"))
        .withColumn("day_of_week", F.dayofweek("date_day"))
        .withColumn("day_name", F.date_format("date_day", "EEEE"))
        .withColumn("week_of_year", F.weekofyear("date_day"))
        .withColumn("is_weekend", F.dayofweek("date_day").isin(1, 7))
        .select(
            "date_key",
            "date_day",
            "year",
            "quarter",
            "month",
            "month_name",
            "year_month_key",
            "year_month",
            "day_of_month",
            "day_of_week",
            "day_name",
            "week_of_year",
            "is_weekend",
        )
    )

    machines = (
        assignments.withColumn("machine_key", F.xxhash64("machine_id"))
        .select("machine_key", "machine_id", "site_id", "client_id", "model")
    )
    sites = (
        assignments.select("site_id", "client_id")
        .distinct()
        .withColumn("site_key", F.xxhash64("client_id", "site_id"))
        .select("site_key", "site_id", "client_id")
    )
    clients = (
        assignments.select("client_id")
        .distinct()
        .withColumn("client_key", F.xxhash64("client_id"))
        .select("client_key", "client_id")
    )
    models = (
        assignments.select("model")
        .distinct()
        .withColumn("model_key", F.xxhash64("model"))
        .select("model_key", "model")
    )
    faults = (
        failures.select("fault_code", "severity")
        .distinct()
        .withColumn("fault_key", F.xxhash64("fault_code", "severity"))
        .withColumn(
            "severity_rank",
            F.when(F.col("severity") == "critical", 4)
            .when(F.col("severity") == "high", 3)
            .when(F.col("severity") == "medium", 2)
            .when(F.col("severity") == "low", 1)
            .otherwise(0),
        )
        .select("fault_key", "fault_code", "severity", "severity_rank")
    )

    date_members = dates.select(
        F.col("date_day").alias("event_date"), "date_key"
    )

    uptime_facts = (
        uptime.join(
            machines.select("machine_id", "machine_key"),
            "machine_id",
            "inner",
        )
        .join(date_members, "event_date", "inner")
        .join(
            sites.select("site_id", "client_id", "site_key"),
            ["site_id", "client_id"],
            "inner",
        )
        .join(clients.select("client_id", "client_key"), "client_id", "inner")
        .join(models.select("model", "model_key"), "model", "inner")
        .withColumn("uptime_fact_key", F.xxhash64("event_date", "machine_id"))
        .withColumn(
            "downtime_pct",
            F.when(
                F.col("observed_minutes") > 0,
                F.round(
                    F.col("downtime_minutes") / F.col("observed_minutes") * 100,
                    2,
                ),
            ).otherwise(F.lit(None).cast("double")),
        )
        .withColumn(
            "maintenance_pct",
            F.when(
                F.col("observed_minutes") > 0,
                F.round(
                    F.col("maintenance_minutes")
                    / F.col("observed_minutes")
                    * 100,
                    2,
                ),
            ).otherwise(F.lit(None).cast("double")),
        )
        .withColumn(
            "idle_pct",
            F.when(
                F.col("observed_minutes") > 0,
                F.round(F.col("idle_minutes") / F.col("observed_minutes") * 100, 2),
            ).otherwise(F.lit(None).cast("double")),
        )
        .select(
            "uptime_fact_key",
            "event_date",
            "date_key",
            "client_key",
            "machine_key",
            "model_key",
            "site_key",
            "running_minutes",
            "idle_minutes",
            "maintenance_minutes",
            "downtime_minutes",
            "observed_minutes",
            "uptime_pct",
            "idle_pct",
            "downtime_pct",
            "maintenance_pct",
            "avg_health_score",
        )
    )

    failure_facts = (
        failures.join(
            machines.select("machine_id", "machine_key"),
            "machine_id",
            "inner",
        )
        .join(date_members, "event_date", "inner")
        .join(
            sites.select("site_id", "client_id", "site_key"),
            ["site_id", "client_id"],
            "inner",
        )
        .join(clients.select("client_id", "client_key"), "client_id", "inner")
        .join(models.select("model", "model_key"), "model", "inner")
        .join(
            faults.select("fault_code", "severity", "fault_key"),
            ["fault_code", "severity"],
            "inner",
        )
        .withColumn("failure_fact_key", F.xxhash64("event_id"))
        .withColumn("failure_event_count", F.lit(1))
        .select(
            "failure_fact_key",
            "event_id",
            "event_date",
            "event_ts_utc",
            "date_key",
            "client_key",
            "machine_key",
            "model_key",
            "site_key",
            "fault_key",
            "failure_event_count",
            "temperature_c",
            "vibration_mm_s",
            "downtime_minutes",
            "maintenance_cost_gbp",
            "part_code",
            "part_quantity",
        )
    )

    return {
        "dim_client": clients,
        "dim_date": dates,
        "dim_fault": faults,
        "dim_machine": machines,
        "dim_model": models,
        "dim_site": sites,
        FAILURE_FACT: failure_facts,
        UPTIME_FACT: uptime_facts,
    }


def _duplicate_count(dataframe: DataFrame, grain: tuple[str, ...]) -> int:
    return int(
        dataframe.groupBy(*grain)
        .count()
        .where(F.col("count") > 1)
        .agg(F.coalesce(F.sum(F.col("count") - 1), F.lit(0)).alias("duplicates"))
        .collect()[0]["duplicates"]
    )


def _null_key_count(dataframe: DataFrame, keys: tuple[str, ...]) -> int:
    predicate = F.lit(False)
    for key in keys:
        predicate = predicate | F.col(key).isNull()
    return int(dataframe.where(predicate).count())


def _unmatched_key_count(
    fact: DataFrame, dimension: DataFrame, *, key: str
) -> int:
    return int(
        fact.where(F.col(key).isNotNull())
        .join(dimension.select(key).distinct(), key, "left_anti")
        .count()
    )


def audit_warehouse(
    *,
    gold_uptime: DataFrame,
    gold_failures: DataFrame,
    warehouse_frames: Mapping[str, DataFrame],
) -> tuple[WarehouseFinding, ...]:
    """Execute source/fact, grain, null-key, and dimension-reference audits."""

    required_frames = {
        "dim_client",
        "dim_date",
        "dim_fault",
        "dim_machine",
        "dim_model",
        "dim_site",
        UPTIME_FACT,
        FAILURE_FACT,
    }
    missing_frames = sorted(required_frames.difference(warehouse_frames))
    if missing_frames:
        raise ValueError(
            f"warehouse frames are missing required datasets: {', '.join(missing_frames)}"
        )

    uptime_fact = warehouse_frames[UPTIME_FACT]
    failure_fact = warehouse_frames[FAILURE_FACT]
    _require_columns(
        uptime_fact,
        {
            "date_key",
            "client_key",
            "machine_key",
            "model_key",
            "site_key",
        },
        label=UPTIME_FACT,
    )
    _require_columns(
        failure_fact,
        {
            "event_id",
            "date_key",
            "client_key",
            "machine_key",
            "model_key",
            "site_key",
            "fault_key",
        },
        label=FAILURE_FACT,
    )

    findings: list[WarehouseFinding] = []
    for dataset, source, fact in (
        (UPTIME_FACT, gold_uptime, uptime_fact),
        (FAILURE_FACT, gold_failures, failure_fact),
    ):
        difference = fact.count() - source.count()
        if difference:
            findings.append(
                WarehouseFinding(
                    code="source_fact_count_mismatch",
                    dataset=dataset,
                    count=int(abs(difference)),
                )
            )

    duplicate_uptime = _duplicate_count(uptime_fact, ("date_key", "machine_key"))
    if duplicate_uptime:
        findings.append(
            WarehouseFinding(
                code="duplicate_fact_grain",
                dataset=UPTIME_FACT,
                count=duplicate_uptime,
            )
        )
    duplicate_failures = _duplicate_count(failure_fact, ("event_id",))
    if duplicate_failures:
        findings.append(
            WarehouseFinding(
                code="duplicate_fact_grain",
                dataset=FAILURE_FACT,
                count=duplicate_failures,
            )
        )

    uptime_keys = ("date_key", "client_key", "machine_key", "model_key", "site_key")
    failure_keys = uptime_keys + ("fault_key",)
    null_uptime = _null_key_count(uptime_fact, uptime_keys)
    if null_uptime:
        findings.append(
            WarehouseFinding(
                code="null_dimension_key", dataset=UPTIME_FACT, count=null_uptime
            )
        )
    null_failures = _null_key_count(failure_fact, failure_keys)
    if null_failures:
        findings.append(
            WarehouseFinding(
                code="null_dimension_key", dataset=FAILURE_FACT, count=null_failures
            )
        )

    for dataset, fact, keys in (
        (UPTIME_FACT, uptime_fact, uptime_keys),
        (FAILURE_FACT, failure_fact, failure_keys),
    ):
        for key in keys:
            dimension_name, dimension_key = _DIMENSION_KEYS[key]
            unmatched = _unmatched_key_count(
                fact,
                warehouse_frames[dimension_name],
                key=dimension_key,
            )
            if unmatched:
                findings.append(
                    WarehouseFinding(
                        code="unmatched_dimension_key",
                        dataset=f"{dataset}.{key}",
                        count=unmatched,
                    )
                )

    return tuple(sorted(findings))
