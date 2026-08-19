"""Shared PySpark medallion transformations and reconciliation evidence.

The functions in this module operate on DataFrames only. They do not read from or
write to a catalog, checkpoint, volume, or external service. Databricks notebooks
can own orchestration and persistence while local Spark tests execute the same
transformation logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType


RAW_MACHINE_EVENT_COLUMNS = (
    "event_id",
    "machine_id",
    "event_ts",
    "site_id",
    "client_id",
    "model",
    "hour_meter",
    "event_type",
    "status",
    "fault_code",
    "severity",
    "temperature_c",
    "vibration_mm_s",
    "fuel_level_pct",
    "duration_minutes",
    "downtime_minutes",
    "maintenance_cost_gbp",
    "part_code",
    "part_quantity",
    "operator_shift",
)


@dataclass(frozen=True)
class SilverReconciliation:
    bronze_rows: int
    valid_rows_before_deduplication: int
    quarantine_rows: int
    silver_rows: int
    deduplicated_rows: int

    @property
    def is_reconciled(self) -> bool:
        return (
            self.bronze_rows
            == self.silver_rows + self.quarantine_rows + self.deduplicated_rows
        )


def raw_machine_event_schema() -> StructType:
    """Return the source-shaped all-string schema used by Bronze ingestion."""

    return StructType(
        [
            StructField(column_name, StringType(), True)
            for column_name in RAW_MACHINE_EVENT_COLUMNS
        ]
    )


def _require_columns(dataframe: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def build_silver_frames(bronze: DataFrame) -> Mapping[str, DataFrame]:
    """Type, validate, quarantine, deduplicate, and enrich Bronze events."""

    _require_columns(
        bronze,
        set(RAW_MACHINE_EVENT_COLUMNS) | {"_ingested_at", "_source_file"},
        label="bronze",
    )

    typed = (
        bronze.withColumn("event_ts_utc", F.to_timestamp("event_ts"))
        .withColumn("event_date", F.to_date("event_ts_utc"))
        .withColumn("hour_meter", F.col("hour_meter").cast("double"))
        .withColumn("temperature_c", F.col("temperature_c").cast("double"))
        .withColumn("vibration_mm_s", F.col("vibration_mm_s").cast("double"))
        .withColumn("fuel_level_pct", F.col("fuel_level_pct").cast("double"))
        .withColumn("duration_minutes", F.col("duration_minutes").cast("int"))
        .withColumn("downtime_minutes", F.col("downtime_minutes").cast("int"))
        .withColumn(
            "maintenance_cost_gbp", F.col("maintenance_cost_gbp").cast("double")
        )
        .withColumn("part_quantity", F.col("part_quantity").cast("int"))
        .withColumn("event_type", F.lower(F.trim("event_type")))
        .withColumn("status", F.upper(F.trim("status")))
        .withColumn("severity", F.lower(F.trim("severity")))
        .withColumn("part_code", F.upper(F.trim("part_code")))
        .withColumn("fault_code", F.upper(F.trim("fault_code")))
    )

    required_columns = (
        "event_id",
        "machine_id",
        "event_ts_utc",
        "site_id",
        "client_id",
    )
    is_valid = F.lit(True)
    for column_name in required_columns:
        value = F.col(column_name)
        is_valid = is_valid & value.isNotNull() & (
            F.length(F.trim(value.cast("string"))) > 0
        )

    valid = typed.where(is_valid)
    quarantine = typed.where(~is_valid).withColumn(
        "quarantine_reason",
        F.lit("Missing or invalid required business key"),
    )

    dedupe_window = Window.partitionBy("event_id").orderBy(
        F.col("_ingested_at").desc_nulls_last(),
        F.col("_source_file").desc_nulls_last(),
    )

    silver = (
        valid.withColumn("_dedupe_rank", F.row_number().over(dedupe_window))
        .where(F.col("_dedupe_rank") == 1)
        .drop("_dedupe_rank")
        .withColumn(
            "is_failure_event",
            (F.col("status") == F.lit("FAULT"))
            | (
                F.col("fault_code").isNotNull()
                & (F.col("fault_code") != F.lit("OK"))
            ),
        )
        .withColumn(
            "health_score",
            F.greatest(
                F.lit(0),
                F.lit(100)
                - F.when(F.col("temperature_c") > 90, 20).otherwise(0)
                - F.when(F.col("vibration_mm_s") > 6, 25).otherwise(0)
                - F.when(F.col("fuel_level_pct") < 20, 10).otherwise(0)
                - F.when(F.col("status") == "FAULT", 30).otherwise(0),
            ),
        )
        .withColumn(
            "maintenance_cost_gbp",
            F.coalesce(F.col("maintenance_cost_gbp"), F.lit(0.0)),
        )
        .withColumn(
            "part_quantity", F.coalesce(F.col("part_quantity"), F.lit(0))
        )
    )

    return {"silver": silver, "quarantine": quarantine}


def reconcile_silver(
    bronze: DataFrame, silver: DataFrame, quarantine: DataFrame
) -> SilverReconciliation:
    """Materialize counts explaining accepted, quarantined, and replay rows."""

    bronze_rows = bronze.count()
    quarantine_rows = quarantine.count()
    silver_rows = silver.count()
    valid_rows = bronze_rows - quarantine_rows
    deduplicated_rows = valid_rows - silver_rows
    if valid_rows < 0 or deduplicated_rows < 0:
        raise ValueError("Silver reconciliation produced impossible negative counts")

    return SilverReconciliation(
        bronze_rows=bronze_rows,
        valid_rows_before_deduplication=valid_rows,
        quarantine_rows=quarantine_rows,
        silver_rows=silver_rows,
        deduplicated_rows=deduplicated_rows,
    )


def build_gold_frames(silver: DataFrame) -> Mapping[str, DataFrame]:
    """Build the BI-facing Gold DataFrames from trusted Silver events."""

    _require_columns(
        silver,
        {
            "event_id",
            "event_date",
            "event_ts_utc",
            "site_id",
            "client_id",
            "machine_id",
            "model",
            "status",
            "fault_code",
            "severity",
            "temperature_c",
            "vibration_mm_s",
            "duration_minutes",
            "downtime_minutes",
            "maintenance_cost_gbp",
            "part_code",
            "part_quantity",
            "health_score",
            "is_failure_event",
        },
        label="silver",
    )

    uptime = (
        silver.groupBy("event_date", "site_id", "client_id", "machine_id", "model")
        .agg(
            F.sum(
                F.when(
                    F.col("status") == "RUNNING", F.col("duration_minutes")
                ).otherwise(0)
            ).alias("running_minutes"),
            F.sum(
                F.when(
                    F.col("status") == "IDLE", F.col("duration_minutes")
                ).otherwise(0)
            ).alias("idle_minutes"),
            F.sum(
                F.when(
                    F.col("status") == "MAINTENANCE", F.col("duration_minutes")
                ).otherwise(0)
            ).alias("maintenance_minutes"),
            F.sum(F.coalesce(F.col("downtime_minutes"), F.lit(0))).alias(
                "downtime_minutes"
            ),
            F.sum(F.coalesce(F.col("duration_minutes"), F.lit(0))).alias(
                "observed_minutes"
            ),
            F.avg("health_score").alias("avg_health_score"),
        )
        .withColumn(
            "uptime_pct",
            F.when(
                F.col("observed_minutes") > 0,
                F.round(
                    F.col("running_minutes") / F.col("observed_minutes") * 100,
                    2,
                ),
            ).otherwise(F.lit(None).cast("double")),
        )
    )

    failure_events = (
        silver.where(F.col("is_failure_event"))
        .select(
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
        )
        .orderBy("event_ts_utc")
    )

    maintenance_costs = (
        silver.groupBy(
            F.date_trunc("month", "event_ts_utc").alias("event_month"),
            "site_id",
            "client_id",
            "model",
        )
        .agg(
            F.count(F.when(F.col("status") == "MAINTENANCE", True)).alias(
                "maintenance_event_count"
            ),
            F.count(F.when(F.col("is_failure_event"), True)).alias(
                "failure_event_count"
            ),
            F.sum("maintenance_cost_gbp").alias("maintenance_cost_gbp"),
            F.sum("downtime_minutes").alias("downtime_minutes"),
        )
        .withColumn("maintenance_cost_gbp", F.round("maintenance_cost_gbp", 2))
    )

    parts_usage = (
        silver.where(
            F.col("part_code").isNotNull()
            & (F.col("part_code") != "NONE")
            & (F.col("part_quantity") > 0)
        )
        .groupBy("event_date", "site_id", "client_id", "model", "part_code")
        .agg(
            F.sum("part_quantity").alias("part_quantity"),
            F.sum("maintenance_cost_gbp").alias("associated_cost_gbp"),
            F.countDistinct("machine_id").alias("machine_count"),
        )
        .withColumn("associated_cost_gbp", F.round("associated_cost_gbp", 2))
    )

    asset_summary = (
        uptime.groupBy("client_id", "site_id", "machine_id", "model")
        .agg(
            F.round(F.avg("uptime_pct"), 2).alias("avg_uptime_pct"),
            F.round(F.avg("avg_health_score"), 2).alias("avg_health_score"),
            F.sum("downtime_minutes").alias("total_downtime_minutes"),
        )
        .join(
            failure_events.groupBy("client_id", "site_id", "machine_id").agg(
                F.count("*").alias("failure_event_count"),
                F.sum("maintenance_cost_gbp").alias("failure_related_cost_gbp"),
            ),
            ["client_id", "site_id", "machine_id"],
            "left",
        )
        .fillna({"failure_event_count": 0, "failure_related_cost_gbp": 0.0})
        .withColumn(
            "failure_related_cost_gbp", F.round("failure_related_cost_gbp", 2)
        )
    )

    return {
        "gold_machine_uptime": uptime,
        "gold_failure_events": failure_events,
        "gold_maintenance_costs": maintenance_costs,
        "gold_parts_usage": parts_usage,
        "gold_client_asset_summary": asset_summary,
    }
