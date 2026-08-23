"""Executable PySpark warehouse construction and reconciliation evidence.

The functions in this module operate on DataFrames only. They do not read from or
write to catalogs, tables, volumes, checkpoints, or external services. Databricks
notebooks can own persistence while local Spark tests execute the same modelling
and audit behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from lakehouse_demo.downtime_semantics import downtime_impact_ratio


UPTIME_FACT = "fact_machine_uptime_daily"
FAILURE_FACT = "fact_machine_failure_event"
UNKNOWN_MEMBER_POLICY = "reject_required_business_identity"

_DIMENSION_KEYS = {
    "date_key": ("dim_date", "date_key"),
    "client_key": ("dim_client", "client_key"),
    "machine_key": ("dim_machine", "machine_key"),
    "model_key": ("dim_model", "model_key"),
    "site_key": ("dim_site", "site_key"),
    "fault_key": ("dim_fault", "fault_key"),
}
_UPTIME_BUSINESS_IDENTITY = (
    "event_date",
    "machine_id",
    "client_id",
    "site_id",
    "model",
)
_FAILURE_BUSINESS_IDENTITY = (
    "event_id",
    "event_date",
    "machine_id",
    "client_id",
    "site_id",
    "model",
    "fault_code",
    "severity",
)
_ASSIGNMENT_COLUMNS = ("site_id", "client_id", "model")


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


def _require_business_identity(
    dataframe: DataFrame,
    columns: tuple[str, ...],
    *,
    label: str,
) -> None:
    """Reject absent business identities rather than inventing unknown members."""

    _require_columns(dataframe, set(columns), label=label)
    predicate = F.lit(False)
    for column in columns:
        predicate = predicate | F.col(column).isNull() | (
            F.length(F.trim(F.col(column).cast("string"))) == 0
        )
    invalid_count = int(dataframe.where(predicate).count())
    if invalid_count:
        raise ValueError(
            f"{label} contains {invalid_count} rows with missing required business identity"
        )


def _assignment_observations(uptime: DataFrame, failures: DataFrame) -> DataFrame:
    observations = (
        uptime.select("event_date", "machine_id", *_ASSIGNMENT_COLUMNS)
        .unionByName(
            failures.select("event_date", "machine_id", *_ASSIGNMENT_COLUMNS),
            allowMissingColumns=False,
        )
        .distinct()
    )
    conflicts = (
        observations.groupBy("machine_id", "event_date")
        .agg(
            F.countDistinct(
                F.struct(*[F.col(column) for column in _ASSIGNMENT_COLUMNS])
            ).alias("assignment_count")
        )
        .where(F.col("assignment_count") > 1)
    )
    if _first_count(conflicts):
        raise ValueError("gold inputs contain conflicting same-day machine assignments")
    return observations


def _machine_assignment_versions(observations: DataFrame) -> DataFrame:
    """Build deterministic SCD2-style versions from dated assignment observations."""

    ordering = Window.partitionBy("machine_id").orderBy("event_date")
    cumulative = ordering.rowsBetween(Window.unboundedPreceding, Window.currentRow)
    previous = {column: F.lag(column).over(ordering) for column in _ASSIGNMENT_COLUMNS}
    changed = F.row_number().over(ordering) == 1
    for column in _ASSIGNMENT_COLUMNS:
        changed = changed | ~F.col(column).eqNullSafe(previous[column])

    segmented = (
        observations.withColumn(
            "_assignment_change",
            F.when(changed, F.lit(1)).otherwise(F.lit(0)),
        )
        .withColumn(
            "assignment_version",
            F.sum("_assignment_change").over(cumulative).cast("int"),
        )
    )
    versions = (
        segmented.groupBy(
            "machine_id",
            "assignment_version",
            *_ASSIGNMENT_COLUMNS,
        )
        .agg(F.min("event_date").alias("valid_from_date"))
    )
    version_order = Window.partitionBy("machine_id").orderBy(
        "valid_from_date", "assignment_version"
    )
    return (
        versions.withColumn(
            "_next_valid_from_date",
            F.lead("valid_from_date").over(version_order),
        )
        .withColumn(
            "valid_to_date",
            F.when(
                F.col("_next_valid_from_date").isNotNull(),
                F.date_sub(F.col("_next_valid_from_date"), 1),
            ).otherwise(F.lit(None).cast("date")),
        )
        .withColumn("is_current", F.col("_next_valid_from_date").isNull())
        .withColumn("machine_key", F.xxhash64("machine_id", "valid_from_date"))
        .drop("_next_valid_from_date")
        .select(
            "machine_key",
            "machine_id",
            "site_id",
            "client_id",
            "model",
            "assignment_version",
            "valid_from_date",
            "valid_to_date",
            "is_current",
        )
    )


def _resolve_machine_version(source: DataFrame, machines: DataFrame, *, label: str) -> DataFrame:
    condition = (
        (F.col("source.machine_id") == F.col("machine.machine_id"))
        & (F.col("source.site_id") == F.col("machine.site_id"))
        & (F.col("source.client_id") == F.col("machine.client_id"))
        & (F.col("source.model") == F.col("machine.model"))
        & (F.col("source.event_date") >= F.col("machine.valid_from_date"))
        & (
            F.col("machine.valid_to_date").isNull()
            | (F.col("source.event_date") <= F.col("machine.valid_to_date"))
        )
    )
    resolved = source.alias("source").join(
        machines.alias("machine"), condition, "left"
    ).select(
        *[F.col(f"source.{column}").alias(column) for column in source.columns],
        F.col("machine.machine_key").alias("machine_key"),
    )
    source_count = int(source.count())
    resolved_count = int(resolved.count())
    if resolved.where(F.col("machine_key").isNull()).count():
        raise ValueError(f"{label} contains rows without a dated machine assignment")
    if resolved_count != source_count:
        raise ValueError(f"{label} machine assignment resolution changed row count")
    return resolved


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
    _require_business_identity(uptime, _UPTIME_BUSINESS_IDENTITY, label="gold uptime")
    _require_business_identity(failures, _FAILURE_BUSINESS_IDENTITY, label="gold failures")

    assignment_observations = _assignment_observations(uptime, failures)
    machines = _machine_assignment_versions(assignment_observations)

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
            "date_key", "date_day", "year", "quarter", "month", "month_name",
            "year_month_key", "year_month", "day_of_month", "day_of_week",
            "day_name", "week_of_year", "is_weekend",
        )
    )
    sites = (
        assignment_observations.select("site_id", "client_id")
        .distinct()
        .withColumn("site_key", F.xxhash64("client_id", "site_id"))
        .select("site_key", "site_id", "client_id")
    )
    clients = (
        assignment_observations.select("client_id")
        .distinct()
        .withColumn("client_key", F.xxhash64("client_id"))
        .select("client_key", "client_id")
    )
    models = (
        assignment_observations.select("model")
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

    uptime_with_machine = _resolve_machine_version(uptime, machines, label="gold uptime")
    failure_with_machine = _resolve_machine_version(failures, machines, label="gold failures")

    uptime_facts = (
        uptime_with_machine.withColumn(
            "date_key", F.date_format("event_date", "yyyyMMdd").cast("int")
        )
        .withColumn("client_key", F.xxhash64("client_id"))
        .withColumn("site_key", F.xxhash64("client_id", "site_id"))
        .withColumn("model_key", F.xxhash64("model"))
        .withColumn("uptime_fact_key", F.xxhash64("event_date", "machine_id"))
        .withColumn("downtime_impact_ratio_pct", downtime_impact_ratio())
        .withColumn(
            "maintenance_pct",
            F.when(
                F.col("observed_minutes") > 0,
                F.round(F.col("maintenance_minutes") / F.col("observed_minutes") * 100, 2),
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
            "uptime_fact_key", "event_date", "date_key", "client_key", "machine_key",
            "model_key", "site_key", "running_minutes", "idle_minutes",
            "maintenance_minutes", "downtime_minutes", "observed_minutes",
            "uptime_pct", "idle_pct", "downtime_impact_ratio_pct",
            "maintenance_pct", "avg_health_score",
        )
    )

    failure_facts = (
        failure_with_machine.withColumn(
            "date_key", F.date_format("event_date", "yyyyMMdd").cast("int")
        )
        .withColumn("client_key", F.xxhash64("client_id"))
        .withColumn("site_key", F.xxhash64("client_id", "site_id"))
        .withColumn("model_key", F.xxhash64("model"))
        .withColumn("fault_key", F.xxhash64("fault_code", "severity"))
        .withColumn("failure_fact_key", F.xxhash64("event_id"))
        .withColumn("failure_event_count", F.lit(1))
        .select(
            "failure_fact_key", "event_id", "event_date", "event_ts_utc", "date_key",
            "client_key", "machine_key", "model_key", "site_key", "fault_key",
            "failure_event_count", "temperature_c", "vibration_mm_s",
            "downtime_minutes", "maintenance_cost_gbp", "part_code", "part_quantity",
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


def _unmatched_key_count(fact: DataFrame, dimension: DataFrame, *, key: str) -> int:
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
        "dim_client", "dim_date", "dim_fault", "dim_machine", "dim_model",
        "dim_site", UPTIME_FACT, FAILURE_FACT,
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
        {"date_key", "client_key", "machine_key", "model_key", "site_key"},
        label=UPTIME_FACT,
    )
    _require_columns(
        failure_fact,
        {"event_id", "date_key", "client_key", "machine_key", "model_key", "site_key", "fault_key"},
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
        findings.append(WarehouseFinding("duplicate_fact_grain", UPTIME_FACT, duplicate_uptime))
    duplicate_failures = _duplicate_count(failure_fact, ("event_id",))
    if duplicate_failures:
        findings.append(WarehouseFinding("duplicate_fact_grain", FAILURE_FACT, duplicate_failures))

    uptime_keys = ("date_key", "client_key", "machine_key", "model_key", "site_key")
    failure_keys = uptime_keys + ("fault_key",)
    null_uptime = _null_key_count(uptime_fact, uptime_keys)
    if null_uptime:
        findings.append(WarehouseFinding("null_dimension_key", UPTIME_FACT, null_uptime))
    null_failures = _null_key_count(failure_fact, failure_keys)
    if null_failures:
        findings.append(WarehouseFinding("null_dimension_key", FAILURE_FACT, null_failures))

    for dataset, fact, keys in (
        (UPTIME_FACT, uptime_fact, uptime_keys),
        (FAILURE_FACT, failure_fact, failure_keys),
    ):
        for key in keys:
            dimension_name, dimension_key = _DIMENSION_KEYS[key]
            unmatched = _unmatched_key_count(fact, warehouse_frames[dimension_name], key=dimension_key)
            if unmatched:
                findings.append(
                    WarehouseFinding(
                        code="unmatched_dimension_key",
                        dataset=f"{dataset}.{key}",
                        count=unmatched,
                    )
                )

    return tuple(sorted(findings))
