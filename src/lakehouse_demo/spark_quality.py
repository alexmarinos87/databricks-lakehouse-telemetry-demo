"""Shared Spark quality checks and bounded, append-ready evidence rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Sequence

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType, StructField, StructType, TimestampType

from lakehouse_demo.downtime_pipeline import materialized_downtime_findings


QUALITY_TABLE_NAMES = (
    "bronze",
    "silver",
    "quarantine",
    "gold_machine_uptime",
    "gold_failure_events",
    "gold_maintenance_costs",
    "gold_parts_usage",
    "gold_client_asset_summary",
    "dim_client",
    "dim_date",
    "dim_fault",
    "dim_machine",
    "dim_model",
    "dim_site",
    "fact_machine_failure_event",
    "fact_machine_uptime_daily",
)
NON_EMPTY_TABLE_NAMES = tuple(name for name in QUALITY_TABLE_NAMES if name != "quarantine")
UPTIME_DIMENSION_KEYS = ("date_key", "client_key", "machine_key", "model_key", "site_key")
FAILURE_DIMENSION_KEYS = UPTIME_DIMENSION_KEYS + ("fault_key",)


@dataclass(frozen=True, order=True)
class QualityCheckResult:
    check_name: str
    status: str
    severity: str
    detail: str
    observed_count: int


def _outcome(
    name: str,
    count: int,
    *,
    pass_detail: str,
    fail_detail: str,
    severity: str = "error",
) -> QualityCheckResult:
    return QualityCheckResult(
        check_name=name,
        status="pass" if count == 0 else "fail",
        severity=severity,
        detail=pass_detail if count == 0 else fail_detail,
        observed_count=int(count),
    )


def _safe(
    name: str,
    operation: Callable[[], QualityCheckResult],
    *,
    severity: str = "error",
) -> QualityCheckResult:
    try:
        return operation()
    except Exception:
        # Never persist provider diagnostics or candidate row values.
        return QualityCheckResult(
            check_name=name,
            status="fail",
            severity=severity,
            detail="Quality check could not be evaluated",
            observed_count=1,
        )


def _missing_columns(dataframe: DataFrame, columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(columns).difference(dataframe.columns)))


def _duplicate_rows(dataframe: DataFrame, grain: Sequence[str]) -> int:
    return int(
        dataframe.groupBy(*grain)
        .count()
        .where(F.col("count") > 1)
        .agg(F.coalesce(F.sum(F.col("count") - 1), F.lit(0)).alias("duplicates"))
        .collect()[0]["duplicates"]
    )


def _null_rows(dataframe: DataFrame, columns: Sequence[str]) -> int:
    predicate = F.lit(False)
    for column in columns:
        predicate = predicate | F.col(column).isNull()
    return int(dataframe.where(predicate).count())


def _silver_event_id_unique(silver: DataFrame) -> QualityCheckResult:
    missing = _missing_columns(silver, ("event_id",))
    if missing:
        return QualityCheckResult(
            "silver_event_id_unique", "fail", "error", "Silver is missing event_id", len(missing)
        )
    return _outcome(
        "silver_event_id_unique",
        _duplicate_rows(silver, ("event_id",)),
        pass_detail="Silver event IDs are unique",
        fail_detail="Silver contains duplicate event IDs",
    )


def _silver_required_fields(silver: DataFrame) -> QualityCheckResult:
    columns = ("event_id", "machine_id", "event_ts_utc", "site_id", "client_id")
    missing = _missing_columns(silver, columns)
    if missing:
        return QualityCheckResult(
            "silver_required_fields_present",
            "fail",
            "error",
            "Silver is missing required columns",
            len(missing),
        )
    predicate = F.lit(False)
    for column in columns:
        predicate = predicate | F.col(column).isNull() | (
            F.length(F.trim(F.col(column).cast("string"))) == 0
        )
    return _outcome(
        "silver_required_fields_present",
        silver.where(predicate).count(),
        pass_detail="Silver required fields are populated",
        fail_detail="Silver contains rows with missing required fields",
    )


def _silver_metric_bounds(silver: DataFrame) -> QualityCheckResult:
    columns = (
        "duration_minutes",
        "downtime_minutes",
        "maintenance_cost_gbp",
        "part_quantity",
        "fuel_level_pct",
    )
    missing = _missing_columns(silver, columns)
    if missing:
        return QualityCheckResult(
            "silver_operational_metrics_in_bounds",
            "fail",
            "error",
            "Silver is missing bounded metric columns",
            len(missing),
        )
    invalid = silver.where(
        (F.col("duration_minutes") < 0)
        | (F.col("downtime_minutes") < 0)
        | (F.col("maintenance_cost_gbp") < 0)
        | (F.col("part_quantity") < 0)
        | (F.col("fuel_level_pct") < 0)
        | (F.col("fuel_level_pct") > 100)
    ).count()
    return _outcome(
        "silver_operational_metrics_in_bounds",
        invalid,
        pass_detail="Silver operational metrics satisfy technical bounds",
        fail_detail="Silver contains metrics outside technical bounds",
    )


def _grain_check(dataframe: DataFrame, *, name: str, grain: Sequence[str]) -> QualityCheckResult:
    missing = _missing_columns(dataframe, grain)
    if missing:
        return QualityCheckResult(name, "fail", "error", "Fact is missing grain columns", len(missing))
    return _outcome(
        name,
        _duplicate_rows(dataframe, grain),
        pass_detail="Warehouse fact grain is unique",
        fail_detail="Warehouse fact contains duplicate grain rows",
    )


def _dimension_key_check(
    dataframe: DataFrame, *, name: str, keys: Sequence[str]
) -> QualityCheckResult:
    missing = _missing_columns(dataframe, keys)
    if missing:
        return QualityCheckResult(
            name, "fail", "error", "Fact is missing dimension-key columns", len(missing)
        )
    return _outcome(
        name,
        _null_rows(dataframe, keys),
        pass_detail="Warehouse fact dimension keys are populated",
        fail_detail="Warehouse fact contains null dimension keys",
    )


def _uptime_percentage_bounds(uptime: DataFrame) -> QualityCheckResult:
    columns = ("uptime_pct", "idle_pct", "maintenance_pct")
    missing = _missing_columns(uptime, columns)
    if missing:
        return QualityCheckResult(
            "uptime_fact_percentage_bounds",
            "fail",
            "error",
            "Uptime fact is missing percentage columns",
            len(missing),
        )
    predicate = F.lit(False)
    for column in columns:
        predicate = predicate | (
            F.col(column).isNotNull() & ((F.col(column) < 0) | (F.col(column) > 100))
        )
    return _outcome(
        "uptime_fact_percentage_bounds",
        uptime.where(predicate).count(),
        pass_detail="Availability and status percentages are within zero and one hundred",
        fail_detail="Availability or status percentages fall outside zero and one hundred",
    )


def _uptime_status_minutes(uptime: DataFrame) -> QualityCheckResult:
    columns = ("running_minutes", "idle_minutes", "maintenance_minutes", "observed_minutes")
    missing = _missing_columns(uptime, columns)
    if missing:
        return QualityCheckResult(
            "uptime_fact_status_minutes_within_observed",
            "fail",
            "error",
            "Uptime fact is missing duration-partition columns",
            len(missing),
        )
    invalid = uptime.where(
        F.coalesce(F.col("running_minutes"), F.lit(0))
        + F.coalesce(F.col("idle_minutes"), F.lit(0))
        + F.coalesce(F.col("maintenance_minutes"), F.lit(0))
        > F.coalesce(F.col("observed_minutes"), F.lit(0))
    ).count()
    return _outcome(
        "uptime_fact_status_minutes_within_observed",
        invalid,
        pass_detail="Status-specific minutes do not exceed observed minutes",
        fail_detail="Status-specific minutes exceed observed minutes",
    )


def _uptime_downtime_semantics(uptime: DataFrame) -> QualityCheckResult:
    findings = materialized_downtime_findings(uptime)
    violation_count = sum(finding.observed_count for finding in findings)
    return _outcome(
        "uptime_fact_downtime_semantics_valid",
        violation_count,
        pass_detail="Attributed downtime fields match the accepted semantic contract",
        fail_detail="Attributed downtime fields violate formula, alias, flag, or version rules",
    )


def _failure_measure_bounds(failure: DataFrame) -> QualityCheckResult:
    columns = ("failure_event_count", "downtime_minutes", "maintenance_cost_gbp", "part_quantity")
    missing = _missing_columns(failure, columns)
    if missing:
        return QualityCheckResult(
            "failure_fact_measures_in_bounds",
            "fail",
            "error",
            "Failure fact is missing invariant columns",
            len(missing),
        )
    invalid = failure.where(
        F.col("failure_event_count").isNull()
        | (F.col("failure_event_count") != 1)
        | (F.col("downtime_minutes") < 0)
        | (F.col("maintenance_cost_gbp") < 0)
        | (F.col("part_quantity") < 0)
    ).count()
    return _outcome(
        "failure_fact_measures_in_bounds",
        invalid,
        pass_detail="Failure fact measures satisfy technical invariants",
        fail_detail="Failure fact measures violate technical invariants",
    )


def evaluate_quality_tables(
    table_frames: Mapping[str, DataFrame],
    *,
    unavailable_tables: Mapping[str, str] | None = None,
    expected_tables: Sequence[str] = QUALITY_TABLE_NAMES,
) -> tuple[QualityCheckResult, ...]:
    """Evaluate medallion and warehouse quality without catalog writes."""

    unavailable = unavailable_tables or {}
    results: list[QualityCheckResult] = []
    readable: dict[str, DataFrame] = {}
    row_counts: dict[str, int] = {}

    for name in expected_tables:
        if name in unavailable or name not in table_frames:
            results.append(
                QualityCheckResult(
                    f"{name}_table_readable",
                    "fail",
                    "error",
                    "Required table could not be read",
                    1,
                )
            )
            continue
        try:
            count = int(table_frames[name].count())
        except Exception:
            results.append(
                QualityCheckResult(
                    f"{name}_table_readable",
                    "fail",
                    "error",
                    "Required table could not be read",
                    1,
                )
            )
            continue
        readable[name] = table_frames[name]
        row_counts[name] = count
        results.append(
            QualityCheckResult(
                f"{name}_table_readable", "pass", "error", "Table is readable", count
            )
        )

    for name in NON_EMPTY_TABLE_NAMES:
        if name in expected_tables and name in row_counts:
            empty_count = 1 if row_counts[name] == 0 else 0
            results.append(
                _outcome(
                    f"{name}_table_populated",
                    empty_count,
                    pass_detail="Required table contains rows",
                    fail_detail="Required table is empty",
                )
            )

    if "silver" in readable:
        silver = readable["silver"]
        results.extend(
            (
                _safe("silver_event_id_unique", lambda: _silver_event_id_unique(silver)),
                _safe("silver_required_fields_present", lambda: _silver_required_fields(silver)),
                _safe(
                    "silver_operational_metrics_in_bounds",
                    lambda: _silver_metric_bounds(silver),
                ),
            )
        )

    if "fact_machine_uptime_daily" in readable:
        uptime = readable["fact_machine_uptime_daily"]
        results.extend(
            (
                _safe(
                    "uptime_fact_grain_unique",
                    lambda: _grain_check(
                        uptime, name="uptime_fact_grain_unique", grain=("date_key", "machine_key")
                    ),
                ),
                _safe(
                    "uptime_fact_dimension_keys_present",
                    lambda: _dimension_key_check(
                        uptime,
                        name="uptime_fact_dimension_keys_present",
                        keys=UPTIME_DIMENSION_KEYS,
                    ),
                ),
                _safe("uptime_fact_percentage_bounds", lambda: _uptime_percentage_bounds(uptime)),
                _safe(
                    "uptime_fact_status_minutes_within_observed",
                    lambda: _uptime_status_minutes(uptime),
                ),
                _safe(
                    "uptime_fact_downtime_semantics_valid",
                    lambda: _uptime_downtime_semantics(uptime),
                ),
            )
        )

    if "fact_machine_failure_event" in readable:
        failure = readable["fact_machine_failure_event"]
        results.extend(
            (
                _safe(
                    "failure_fact_grain_unique",
                    lambda: _grain_check(
                        failure, name="failure_fact_grain_unique", grain=("event_id",)
                    ),
                ),
                _safe(
                    "failure_fact_dimension_keys_present",
                    lambda: _dimension_key_check(
                        failure,
                        name="failure_fact_dimension_keys_present",
                        keys=FAILURE_DIMENSION_KEYS,
                    ),
                ),
                _safe(
                    "failure_fact_measures_in_bounds",
                    lambda: _failure_measure_bounds(failure),
                ),
            )
        )

    return tuple(sorted(results))


QUALITY_RESULT_SCHEMA = StructType(
    [
        StructField("quality_run_id", StringType(), False),
        StructField("checked_at", TimestampType(), False),
        StructField("check_name", StringType(), False),
        StructField("status", StringType(), False),
        StructField("severity", StringType(), False),
        StructField("detail", StringType(), False),
        StructField("observed_count", LongType(), False),
    ]
)


def quality_results_dataframe(
    spark: SparkSession,
    results: Sequence[QualityCheckResult],
    *,
    quality_run_id: str,
    checked_at: datetime,
) -> DataFrame:
    if not quality_run_id.strip():
        raise ValueError("quality_run_id must be populated")
    if not results:
        raise ValueError("at least one quality result is required")
    return spark.createDataFrame(
        [
            (
                quality_run_id,
                checked_at,
                result.check_name,
                result.status,
                result.severity,
                result.detail,
                int(result.observed_count),
            )
            for result in results
        ],
        schema=QUALITY_RESULT_SCHEMA,
    )


def summarize_quality_results(results: DataFrame) -> DataFrame:
    required = {"quality_run_id", "checked_at", "status", "severity"}
    missing = sorted(required.difference(results.columns))
    if missing:
        raise ValueError("quality results are missing summary columns: " + ", ".join(missing))
    return (
        results.groupBy("quality_run_id", "checked_at")
        .agg(
            F.count(F.lit(1)).alias("check_count"),
            F.sum(F.when(F.col("status") == "pass", 1).otherwise(0)).alias(
                "passed_check_count"
            ),
            F.sum(F.when(F.col("status") == "fail", 1).otherwise(0)).alias(
                "failed_check_count"
            ),
            F.sum(
                F.when(
                    (F.col("status") == "fail") & (F.col("severity") == "error"), 1
                ).otherwise(0)
            ).alias("failed_error_check_count"),
            F.sum(
                F.when(
                    (F.col("status") == "fail") & (F.col("severity") != "error"), 1
                ).otherwise(0)
            ).alias("failed_warning_check_count"),
        )
        .withColumn("all_error_checks_passed", F.col("failed_error_check_count") == 0)
        .select(
            "quality_run_id",
            "checked_at",
            "check_count",
            "passed_check_count",
            "failed_check_count",
            "failed_error_check_count",
            "failed_warning_check_count",
            "all_error_checks_passed",
        )
    )
