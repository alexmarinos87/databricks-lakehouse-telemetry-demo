# Databricks notebook source
# MAGIC %md
# MAGIC # 01 - Bronze ingest
# MAGIC
# MAGIC Ingest raw construction equipment telemetry into a Delta bronze table.
# MAGIC Bronze keeps the source-shaped records and adds lineage metadata.

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import StructField, StructType, StringType

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")
dbutils.widgets.text("source_path", "dbfs:/FileStore/lakehouse_demo/sample_machine_events.csv")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
source_path = dbutils.widgets.get("source_path")

bronze_table = f"{catalog}.{schema}.bronze_machine_events"

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

# COMMAND ----------

raw_schema = StructType(
    [
        StructField("event_id", StringType(), True),
        StructField("machine_id", StringType(), True),
        StructField("event_ts", StringType(), True),
        StructField("site_id", StringType(), True),
        StructField("client_id", StringType(), True),
        StructField("model", StringType(), True),
        StructField("hour_meter", StringType(), True),
        StructField("event_type", StringType(), True),
        StructField("status", StringType(), True),
        StructField("fault_code", StringType(), True),
        StructField("severity", StringType(), True),
        StructField("temperature_c", StringType(), True),
        StructField("vibration_mm_s", StringType(), True),
        StructField("fuel_level_pct", StringType(), True),
        StructField("duration_minutes", StringType(), True),
        StructField("downtime_minutes", StringType(), True),
        StructField("maintenance_cost_gbp", StringType(), True),
        StructField("part_code", StringType(), True),
        StructField("part_quantity", StringType(), True),
        StructField("operator_shift", StringType(), True),
    ]
)

bronze_df = (
    spark.read.format("csv")
    .option("header", True)
    .schema(raw_schema)
    .load(source_path)
    .withColumn("_ingested_at", F.current_timestamp())
    .withColumn("_source_file", F.input_file_name())
)

# COMMAND ----------

(
    bronze_df.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(bronze_table)
)

row_count = spark.table(bronze_table).count()
if row_count == 0:
    raise ValueError(f"No records were ingested from {source_path}")

display(spark.table(bronze_table))
