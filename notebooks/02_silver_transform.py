# Databricks notebook source
# MAGIC %md
# MAGIC # 02 - Silver transform
# MAGIC
# MAGIC Clean, type, deduplicate and validate raw machine event data.

# COMMAND ----------

from pyspark.sql import Window
from pyspark.sql import functions as F

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

bronze_table = f"{catalog}.{schema}.bronze_machine_events"
silver_table = f"{catalog}.{schema}.silver_machine_events"
quarantine_table = f"{catalog}.{schema}.silver_quarantine_machine_events"

# COMMAND ----------

bronze = spark.table(bronze_table)

typed = (
    bronze.withColumn("event_ts_utc", F.to_timestamp("event_ts"))
    .withColumn("event_date", F.to_date("event_ts_utc"))
    .withColumn("hour_meter", F.col("hour_meter").cast("double"))
    .withColumn("temperature_c", F.col("temperature_c").cast("double"))
    .withColumn("vibration_mm_s", F.col("vibration_mm_s").cast("double"))
    .withColumn("fuel_level_pct", F.col("fuel_level_pct").cast("double"))
    .withColumn("duration_minutes", F.col("duration_minutes").cast("int"))
    .withColumn("downtime_minutes", F.col("downtime_minutes").cast("int"))
    .withColumn("maintenance_cost_gbp", F.col("maintenance_cost_gbp").cast("double"))
    .withColumn("part_quantity", F.col("part_quantity").cast("int"))
    .withColumn("event_type", F.lower(F.trim("event_type")))
    .withColumn("status", F.upper(F.trim("status")))
    .withColumn("severity", F.lower(F.trim("severity")))
    .withColumn("part_code", F.upper(F.trim("part_code")))
    .withColumn("fault_code", F.upper(F.trim("fault_code")))
)

required_columns = ["event_id", "machine_id", "event_ts_utc", "site_id", "client_id"]
is_valid = F.lit(True)
for column_name in required_columns:
    is_valid = is_valid & F.col(column_name).isNotNull() & (F.length(F.trim(F.col(column_name).cast("string"))) > 0)

valid = typed.where(is_valid)
quarantine = typed.where(~is_valid).withColumn(
    "quarantine_reason",
    F.lit("Missing one or more required business keys"),
)

# COMMAND ----------

dedupe_window = Window.partitionBy("event_id").orderBy(F.col("_ingested_at").desc(), F.col("_source_file").desc())

silver = (
    valid.withColumn("_dedupe_rank", F.row_number().over(dedupe_window))
    .where(F.col("_dedupe_rank") == 1)
    .drop("_dedupe_rank")
    .withColumn(
        "is_failure_event",
        (F.col("status") == F.lit("FAULT")) | ((F.col("fault_code").isNotNull()) & (F.col("fault_code") != F.lit("OK"))),
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
    .withColumn("maintenance_cost_gbp", F.coalesce(F.col("maintenance_cost_gbp"), F.lit(0.0)))
    .withColumn("part_quantity", F.coalesce(F.col("part_quantity"), F.lit(0)))
)

# COMMAND ----------

(
    silver.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(silver_table)
)

(
    quarantine.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(quarantine_table)
)

display(spark.table(silver_table).orderBy("event_ts_utc", "machine_id"))
