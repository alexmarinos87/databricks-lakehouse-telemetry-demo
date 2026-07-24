# Databricks notebook source
# MAGIC %md
# MAGIC # 07 - Warehouse model
# MAGIC
# MAGIC Publish a small star schema from the medallion layer for straightforward BI queries.

# COMMAND ----------

from pyspark.sql import functions as F

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

gold_uptime = f"{catalog}.{schema}.gold_machine_uptime"
dim_machine = f"{catalog}.{schema}.dim_machine"
fact_machine_uptime = f"{catalog}.{schema}.fact_machine_uptime_daily"

# COMMAND ----------

uptime = spark.table(gold_uptime)

machines = (
    uptime.select("machine_id", "site_id", "client_id", "model")
    .dropDuplicates(["machine_id"])
    .withColumn("machine_key", F.xxhash64("machine_id"))
    .select("machine_key", "machine_id", "site_id", "client_id", "model")
)

facts = (
    uptime.join(machines.select("machine_id", "machine_key"), "machine_id")
    .select(
        "event_date",
        "machine_key",
        "running_minutes",
        "idle_minutes",
        "maintenance_minutes",
        "downtime_minutes",
        "observed_minutes",
        "uptime_pct",
        "avg_health_score",
    )
)

# COMMAND ----------

for dataframe, table_name in [
    (machines, dim_machine),
    (facts, fact_machine_uptime),
]:
    (
        dataframe.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", True)
        .saveAsTable(table_name)
    )

display(spark.table(fact_machine_uptime).orderBy(F.desc("event_date")))
