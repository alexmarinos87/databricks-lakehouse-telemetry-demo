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
gold_failure_events = f"{catalog}.{schema}.gold_failure_events"
dim_client = f"{catalog}.{schema}.dim_client"
dim_date = f"{catalog}.{schema}.dim_date"
dim_fault = f"{catalog}.{schema}.dim_fault"
dim_machine = f"{catalog}.{schema}.dim_machine"
dim_model = f"{catalog}.{schema}.dim_model"
dim_site = f"{catalog}.{schema}.dim_site"
fact_machine_failure_event = f"{catalog}.{schema}.fact_machine_failure_event"
fact_machine_uptime = f"{catalog}.{schema}.fact_machine_uptime_daily"

# COMMAND ----------

uptime = spark.table(gold_uptime)
failures = spark.table(gold_failure_events)

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
    uptime.select("machine_id", "site_id", "client_id", "model")
    .dropDuplicates(["machine_id"])
    .withColumn("machine_key", F.xxhash64("machine_id"))
    .select("machine_key", "machine_id", "site_id", "client_id", "model")
)

sites = (
    uptime.select("site_id", "client_id")
    .dropDuplicates(["site_id", "client_id"])
    .withColumn("site_key", F.xxhash64("client_id", "site_id"))
    .select("site_key", "site_id", "client_id")
)

clients = (
    uptime.select("client_id")
    .dropDuplicates(["client_id"])
    .withColumn("client_key", F.xxhash64("client_id"))
    .select("client_key", "client_id")
)

models = (
    uptime.select("model")
    .dropDuplicates(["model"])
    .withColumn("model_key", F.xxhash64("model"))
    .select("model_key", "model")
)

faults = (
    failures.select("fault_code", "severity")
    .dropDuplicates(["fault_code", "severity"])
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

uptime_facts = (
    uptime.join(machines.select("machine_id", "machine_key"), "machine_id")
    .join(dates.select("date_day", "date_key"), uptime.event_date == dates.date_day)
    .join(sites.select("site_id", "client_id", "site_key"), ["site_id", "client_id"])
    .join(clients.select("client_id", "client_key"), "client_id")
    .join(models.select("model", "model_key"), "model")
    .withColumn("uptime_fact_key", F.xxhash64("event_date", "machine_id"))
    .withColumn(
        "downtime_pct",
        F.when(
            F.col("observed_minutes") > 0,
            F.round(F.col("downtime_minutes") / F.col("observed_minutes") * 100, 2),
        ).otherwise(F.lit(None).cast("double")),
    )
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
    failures.join(machines.select("machine_id", "machine_key"), "machine_id")
    .join(dates.select("date_day", "date_key"), failures.event_date == dates.date_day)
    .join(sites.select("site_id", "client_id", "site_key"), ["site_id", "client_id"])
    .join(clients.select("client_id", "client_key"), "client_id")
    .join(models.select("model", "model_key"), "model")
    .join(faults.select("fault_code", "severity", "fault_key"), ["fault_code", "severity"])
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

# COMMAND ----------

for dataframe, table_name in [
    (clients, dim_client),
    (dates, dim_date),
    (faults, dim_fault),
    (machines, dim_machine),
    (models, dim_model),
    (sites, dim_site),
    (failure_facts, fact_machine_failure_event),
    (uptime_facts, fact_machine_uptime),
]:
    (
        dataframe.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", True)
        .saveAsTable(table_name)
    )

display(spark.table(fact_machine_failure_event).orderBy(F.desc("event_ts_utc")))
