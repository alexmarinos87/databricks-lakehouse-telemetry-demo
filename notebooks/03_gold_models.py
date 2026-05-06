# Databricks notebook source
# MAGIC %md
# MAGIC # 03 - Gold models
# MAGIC
# MAGIC Build BI-ready aggregate Delta tables from the cleaned silver events.

# COMMAND ----------

from pyspark.sql import functions as F

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

silver_table = f"{catalog}.{schema}.silver_machine_events"

gold_machine_uptime = f"{catalog}.{schema}.gold_machine_uptime"
gold_failure_events = f"{catalog}.{schema}.gold_failure_events"
gold_maintenance_costs = f"{catalog}.{schema}.gold_maintenance_costs"
gold_parts_usage = f"{catalog}.{schema}.gold_parts_usage"
gold_client_asset_summary = f"{catalog}.{schema}.gold_client_asset_summary"

# COMMAND ----------

silver = spark.table(silver_table)

# COMMAND ----------

uptime = (
    silver.groupBy("event_date", "site_id", "client_id", "machine_id", "model")
    .agg(
        F.sum(F.when(F.col("status") == "RUNNING", F.col("duration_minutes")).otherwise(0)).alias("running_minutes"),
        F.sum(F.when(F.col("status") == "IDLE", F.col("duration_minutes")).otherwise(0)).alias("idle_minutes"),
        F.sum(F.when(F.col("status") == "MAINTENANCE", F.col("duration_minutes")).otherwise(0)).alias("maintenance_minutes"),
        F.sum(F.coalesce(F.col("downtime_minutes"), F.lit(0))).alias("downtime_minutes"),
        F.sum(F.coalesce(F.col("duration_minutes"), F.lit(0))).alias("observed_minutes"),
        F.avg("health_score").alias("avg_health_score"),
    )
    .withColumn(
        "uptime_pct",
        F.when(
            F.col("observed_minutes") > 0,
            F.round(F.col("running_minutes") / F.col("observed_minutes") * 100, 2),
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
        F.count(F.when(F.col("status") == "MAINTENANCE", True)).alias("maintenance_event_count"),
        F.count(F.when(F.col("is_failure_event"), True)).alias("failure_event_count"),
        F.sum("maintenance_cost_gbp").alias("maintenance_cost_gbp"),
        F.sum("downtime_minutes").alias("downtime_minutes"),
    )
    .withColumn("maintenance_cost_gbp", F.round("maintenance_cost_gbp", 2))
)

parts_usage = (
    silver.where((F.col("part_code").isNotNull()) & (F.col("part_code") != "NONE") & (F.col("part_quantity") > 0))
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
    .withColumn("failure_related_cost_gbp", F.round("failure_related_cost_gbp", 2))
)

# COMMAND ----------

for dataframe, table_name in [
    (uptime, gold_machine_uptime),
    (failure_events, gold_failure_events),
    (maintenance_costs, gold_maintenance_costs),
    (parts_usage, gold_parts_usage),
    (asset_summary, gold_client_asset_summary),
]:
    (
        dataframe.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", True)
        .saveAsTable(table_name)
    )

display(spark.table(gold_client_asset_summary).orderBy("client_id", "site_id", "machine_id"))
