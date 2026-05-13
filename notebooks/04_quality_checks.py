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


for logical_name, table_name in tables.items():
    try:
        count = spark.table(table_name).count()
        add_result(f"{logical_name}_table_exists", "pass", f"{table_name} contains {count} rows", "error")
    except Exception as exc:
        add_result(f"{logical_name}_table_exists", "fail", f"{table_name} could not be read: {exc}", "error")

silver = spark.table(tables["silver"])

duplicate_events = (
    silver.groupBy("event_id")
    .count()
    .where(F.col("count") > 1)
    .count()
)
add_result(
    "silver_event_id_unique",
    "pass" if duplicate_events == 0 else "fail",
    f"{duplicate_events} duplicated event_id values found in silver",
)

required_nulls = silver.where(
    F.col("event_id").isNull()
    | F.col("machine_id").isNull()
    | F.col("event_ts_utc").isNull()
    | F.col("site_id").isNull()
    | F.col("client_id").isNull()
).count()
add_result(
    "silver_required_fields_present",
    "pass" if required_nulls == 0 else "fail",
    f"{required_nulls} silver rows have missing required fields",
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

display(results_df.orderBy("status", "check_name"))

failed_error_checks = results_df.where((F.col("status") == "fail") & (F.col("severity") == "error")).count()
if failed_error_checks:
    raise AssertionError(f"{failed_error_checks} error-level data quality checks failed")
