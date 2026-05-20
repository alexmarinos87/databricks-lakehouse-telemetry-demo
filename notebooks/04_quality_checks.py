# Databricks notebook source
# MAGIC %md
# MAGIC # 04 - Quality checks
# MAGIC
# MAGIC Run basic validation checks across bronze, silver and gold tables.

# COMMAND ----------

from pyspark.sql import Row
from pyspark.sql import functions as F

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

tables = {
    "bronze": f"{catalog}.{schema}.bronze_machine_events",
    "silver": f"{catalog}.{schema}.silver_machine_events",
    "quarantine": f"{catalog}.{schema}.silver_quarantine_machine_events",
    "gold_machine_uptime": f"{catalog}.{schema}.gold_machine_uptime",
    "gold_failure_events": f"{catalog}.{schema}.gold_failure_events",
    "gold_maintenance_costs": f"{catalog}.{schema}.gold_maintenance_costs",
    "gold_parts_usage": f"{catalog}.{schema}.gold_parts_usage",
    "gold_client_asset_summary": f"{catalog}.{schema}.gold_client_asset_summary",
}

quality_table = f"{catalog}.{schema}.quality_check_results"
quality_history_table = f"{catalog}.{schema}.quality_metric_history"

# COMMAND ----------

results = []


def add_result(check_name, status, detail, severity="error"):
    results.append(
        Row(
            check_name=check_name,
            status=status,
            severity=severity,
            detail=detail,
        )
    )


def add_uniqueness_check(df, check_name, table_label, key_columns, severity="error"):
    duplicate_keys = df.groupBy(*key_columns).count().where(F.col("count") > 1)
    duplicate_summary = duplicate_keys.agg(
        F.count(F.lit(1)).alias("duplicate_key_count"),
        F.coalesce(F.sum("count"), F.lit(0)).alias("duplicate_row_count"),
    ).collect()[0]

    duplicate_key_count = duplicate_summary["duplicate_key_count"]
    duplicate_row_count = duplicate_summary["duplicate_row_count"]
    key_label = ", ".join(key_columns)

    if duplicate_key_count == 0:
        add_result(
            check_name,
            "pass",
            f"{table_label} has unique {key_label} values",
            severity,
        )
    else:
        add_result(
            check_name,
            "fail",
            f"{table_label} has {duplicate_key_count} duplicated {key_label} values across {duplicate_row_count} rows",
            severity,
        )


def add_required_fields_check(df, check_name, table_label, required_columns, severity="error"):
    missing_row_condition = F.lit(False)
    missing_count_expressions = []

    for column_name in required_columns:
        missing_condition = F.col(column_name).isNull() | (F.length(F.trim(F.col(column_name).cast("string"))) == 0)
        missing_row_condition = missing_row_condition | missing_condition
        missing_count_expressions.append(
            F.coalesce(F.sum(F.when(missing_condition, F.lit(1)).otherwise(F.lit(0))), F.lit(0)).alias(column_name)
        )

    missing_rows = df.where(missing_row_condition).count()
    missing_counts = df.agg(*missing_count_expressions).collect()[0].asDict()
    missing_detail = ", ".join(
        f"{column_name}={missing_counts[column_name]}"
        for column_name in required_columns
        if missing_counts[column_name] > 0
    )

    if missing_rows == 0:
        add_result(
            check_name,
            "pass",
            f"{table_label} has populated required fields: {', '.join(required_columns)}",
            severity,
        )
    else:
        add_result(
            check_name,
            "fail",
            f"{table_label} has {missing_rows} rows with missing required fields ({missing_detail})",
            severity,
        )


for logical_name, table_name in tables.items():
    try:
        count = spark.table(table_name).count()
        add_result(f"{logical_name}_table_exists", "pass", f"{table_name} contains {count} rows", "error")
    except Exception as exc:
        add_result(f"{logical_name}_table_exists", "fail", f"{table_name} could not be read: {exc}", "error")

silver = spark.table(tables["silver"])

add_uniqueness_check(
    silver,
    "silver_event_id_unique",
    "silver_machine_events",
    ["event_id"],
)

add_required_fields_check(
    silver,
    "silver_required_fields_present",
    "silver_machine_events",
    ["event_id", "machine_id", "event_ts_utc", "site_id", "client_id"],
)

negative_metrics = silver.where(
    (F.col("duration_minutes") < 0)
    | (F.col("downtime_minutes") < 0)
    | (F.col("maintenance_cost_gbp") < 0)
    | (F.col("fuel_level_pct") < 0)
).count()
add_result(
    "silver_metrics_non_negative",
    "pass" if negative_metrics == 0 else "fail",
    f"{negative_metrics} silver rows have negative operational metrics",
)

empty_gold_tables = [
    name
    for name in [
        "gold_machine_uptime",
        "gold_failure_events",
        "gold_maintenance_costs",
        "gold_parts_usage",
        "gold_client_asset_summary",
    ]
    if spark.table(tables[name]).count() == 0
]
add_result(
    "gold_tables_populated",
    "pass" if not empty_gold_tables else "fail",
    "All gold tables contain rows" if not empty_gold_tables else f"Empty gold tables: {', '.join(empty_gold_tables)}",
)

# COMMAND ----------

results_df = (
    spark.createDataFrame(results)
    .withColumn("checked_at", F.current_timestamp())
    .select("checked_at", "check_name", "status", "severity", "detail")
)

(
    results_df.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(quality_table)
)

quality_history_df = (
    results_df.groupBy("checked_at")
    .agg(
        F.count(F.lit(1)).alias("check_count"),
        F.sum(F.when(F.col("status") == "pass", F.lit(1)).otherwise(F.lit(0))).alias(
            "passed_check_count"
        ),
        F.sum(F.when(F.col("status") == "fail", F.lit(1)).otherwise(F.lit(0))).alias(
            "failed_check_count"
        ),
        F.sum(
            F.when(
                (F.col("status") == "fail") & (F.col("severity") == "error"),
                F.lit(1),
            ).otherwise(F.lit(0))
        ).alias("failed_error_check_count"),
        F.sum(
            F.when(
                (F.col("status") == "fail") & (F.col("severity") != "error"),
                F.lit(1),
            ).otherwise(F.lit(0))
        ).alias("failed_warning_check_count"),
    )
    .withColumn("all_error_checks_passed", F.col("failed_error_check_count") == 0)
    .select(
        "checked_at",
        "check_count",
        "passed_check_count",
        "failed_check_count",
        "failed_error_check_count",
        "failed_warning_check_count",
        "all_error_checks_passed",
    )
)

(
    quality_history_df.write.format("delta")
    .mode("append")
    .option("mergeSchema", True)
    .saveAsTable(quality_history_table)
)

display(results_df.orderBy("status", "check_name"))

failed_error_checks = results_df.where((F.col("status") == "fail") & (F.col("severity") == "error")).count()
if failed_error_checks:
    raise AssertionError(f"{failed_error_checks} error-level data quality checks failed")
