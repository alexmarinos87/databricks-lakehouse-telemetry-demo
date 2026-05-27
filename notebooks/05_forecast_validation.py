# Databricks notebook source
# MAGIC %md
# MAGIC # 05 - Forecast validation
# MAGIC
# MAGIC Build transparent downtime forecast outputs with backtest evidence.
# MAGIC
# MAGIC This notebook intentionally uses a simple rolling-mean baseline so that
# MAGIC forecast assumptions, errors and readiness flags are inspectable before
# MAGIC results are used in BI narratives.

# COMMAND ----------

from pyspark.sql import Window
from pyspark.sql import functions as F

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")
dbutils.widgets.text("baseline_window_days", "2")
dbutils.widgets.text("forecast_horizon_days", "1")
dbutils.widgets.text("min_validation_observations", "2")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")


def get_positive_int_widget(widget_name):
    value = int(dbutils.widgets.get(widget_name))
    if value < 1:
        raise ValueError(f"{widget_name} must be a positive integer")
    return value


baseline_window_days = get_positive_int_widget("baseline_window_days")
forecast_horizon_days = get_positive_int_widget("forecast_horizon_days")
min_validation_observations = get_positive_int_widget("min_validation_observations")

gold_machine_uptime = f"{catalog}.{schema}.gold_machine_uptime"
forecast_validation_table = f"{catalog}.{schema}.gold_downtime_forecast_validation"
forecast_table = f"{catalog}.{schema}.gold_downtime_forecast"

segment_columns = ["site_id", "client_id", "model"]
model_name = "rolling_mean_baseline"
model_notes = (
    "Forecast uses the rolling average of recent daily downtime by site, client and model. "
    "Use validation metrics and readiness flags before presenting results externally."
)

# COMMAND ----------

uptime = spark.table(gold_machine_uptime)

daily_actuals = (
    uptime.groupBy("event_date", *segment_columns)
    .agg(
        F.sum(F.coalesce(F.col("downtime_minutes"), F.lit(0))).alias("actual_downtime_minutes"),
        F.countDistinct("machine_id").alias("machine_count"),
        F.round(F.avg("uptime_pct"), 2).alias("avg_uptime_pct"),
        F.round(F.avg("avg_health_score"), 2).alias("avg_health_score"),
    )
    .where(F.col("event_date").isNotNull())
)

if daily_actuals.count() == 0:
    raise ValueError(f"No daily uptime records were available in {gold_machine_uptime}")

# COMMAND ----------

validation_window = (
    Window.partitionBy(*segment_columns)
    .orderBy("event_date")
    .rowsBetween(-baseline_window_days, -1)
)

validation_base = (
    daily_actuals.withColumn(
        "forecast_downtime_minutes",
        F.avg("actual_downtime_minutes").over(validation_window),
    )
    .withColumn("history_day_count", F.count("actual_downtime_minutes").over(validation_window))
    .where(F.col("history_day_count") >= 1)
    .withColumn("forecast_downtime_minutes", F.round("forecast_downtime_minutes", 2))
    .withColumn(
        "absolute_error_minutes",
        F.round(F.abs(F.col("actual_downtime_minutes") - F.col("forecast_downtime_minutes")), 2),
    )
    .withColumn(
        "squared_error_minutes",
        F.pow(F.col("actual_downtime_minutes") - F.col("forecast_downtime_minutes"), 2),
    )
    .withColumn(
        "absolute_percentage_error",
        F.when(
            F.col("actual_downtime_minutes") > 0,
            F.abs(F.col("actual_downtime_minutes") - F.col("forecast_downtime_minutes"))
            / F.col("actual_downtime_minutes"),
        ),
    )
    .withColumn(
        "residual_minutes",
        F.round(F.col("actual_downtime_minutes") - F.col("forecast_downtime_minutes"), 2),
    )
    .withColumn("model_name", F.lit(model_name))
    .withColumn("baseline_window_days", F.lit(baseline_window_days))
    .withColumn("validation_generated_at", F.current_timestamp())
    .select(
        "validation_generated_at",
        "model_name",
        "baseline_window_days",
        "event_date",
        *segment_columns,
        "machine_count",
        "actual_downtime_minutes",
        "forecast_downtime_minutes",
        "history_day_count",
        "absolute_error_minutes",
        "squared_error_minutes",
        "absolute_percentage_error",
        "residual_minutes",
    )
)

validation_metrics = (
    validation_base.groupBy(*segment_columns)
    .agg(
        F.count(F.lit(1)).alias("validation_observation_count"),
        F.round(F.avg("absolute_error_minutes"), 2).alias("mae_downtime_minutes"),
        F.round(F.sqrt(F.avg("squared_error_minutes")), 2).alias("rmse_downtime_minutes"),
        F.round(F.avg("absolute_percentage_error") * 100, 2).alias("mape_pct"),
        F.round(F.stddev_samp("residual_minutes"), 2).alias("residual_stddev_minutes"),
        F.max("event_date").alias("latest_validation_date"),
    )
)

validation = (
    validation_base.join(
        validation_metrics.select(
            *segment_columns,
            "mae_downtime_minutes",
            "residual_stddev_minutes",
        ),
        segment_columns,
        "left",
    )
    .withColumn(
        "validation_interval_padding_minutes",
        F.coalesce(F.col("residual_stddev_minutes"), F.col("mae_downtime_minutes"), F.lit(0.0)),
    )
    .withColumn(
        "validation_interval_lower_minutes",
        F.greatest(
            F.lit(0.0),
            F.round(F.col("forecast_downtime_minutes") - F.col("validation_interval_padding_minutes"), 2),
        ),
    )
    .withColumn(
        "validation_interval_upper_minutes",
        F.round(F.col("forecast_downtime_minutes") + F.col("validation_interval_padding_minutes"), 2),
    )
    .withColumn(
        "covered_by_validation_interval",
        (F.col("actual_downtime_minutes") >= F.col("validation_interval_lower_minutes"))
        & (F.col("actual_downtime_minutes") <= F.col("validation_interval_upper_minutes")),
    )
    .select(
        "validation_generated_at",
        "model_name",
        "baseline_window_days",
        "event_date",
        *segment_columns,
        "machine_count",
        "actual_downtime_minutes",
        "forecast_downtime_minutes",
        "history_day_count",
        "absolute_error_minutes",
        "squared_error_minutes",
        "absolute_percentage_error",
        "residual_minutes",
        "validation_interval_lower_minutes",
        "validation_interval_upper_minutes",
        "covered_by_validation_interval",
    )
)

interval_coverage = (
    validation.groupBy(*segment_columns)
    .agg(
        F.round(
            F.avg(F.when(F.col("covered_by_validation_interval"), F.lit(1.0)).otherwise(F.lit(0.0))) * 100,
            2,
        ).alias("backtest_interval_coverage_pct")
    )
)

validation_summary = (
    validation_metrics.join(interval_coverage, segment_columns, "left")
    .withColumn(
        "meets_min_validation_samples",
        F.col("validation_observation_count") >= F.lit(min_validation_observations),
    )
)

# COMMAND ----------

history_rank_window = Window.partitionBy(*segment_columns).orderBy(F.col("event_date").desc())

latest_history = (
    daily_actuals.withColumn("history_rank", F.row_number().over(history_rank_window))
    .where(F.col("history_rank") <= baseline_window_days)
    .groupBy(*segment_columns)
    .agg(
        F.max("event_date").alias("latest_actual_date"),
        F.count(F.lit(1)).alias("forecast_history_day_count"),
        F.round(F.avg("actual_downtime_minutes"), 2).alias("forecast_downtime_minutes"),
        F.round(F.avg("avg_uptime_pct"), 2).alias("recent_avg_uptime_pct"),
        F.round(F.avg("avg_health_score"), 2).alias("recent_avg_health_score"),
        F.max("machine_count").alias("machine_count"),
    )
)

forecast = (
    latest_history.join(validation_summary, segment_columns, "left")
    .withColumn(
        "validation_observation_count",
        F.coalesce(F.col("validation_observation_count"), F.lit(0)),
    )
    .withColumn(
        "meets_min_validation_samples",
        F.coalesce(F.col("meets_min_validation_samples"), F.lit(False)),
    )
    .withColumn(
        "forecast_status",
        F.when(F.col("meets_min_validation_samples"), F.lit("validated_baseline")).otherwise(
            F.lit("insufficient_validation_history")
        ),
    )
    .withColumn(
        "prediction_error_padding_minutes",
        F.coalesce(F.col("residual_stddev_minutes"), F.col("mae_downtime_minutes"), F.lit(0.0)),
    )
    .withColumn("forecast_date", F.date_add(F.col("latest_actual_date"), forecast_horizon_days))
    .withColumn(
        "prediction_interval_lower_minutes",
        F.greatest(
            F.lit(0.0),
            F.round(F.col("forecast_downtime_minutes") - F.col("prediction_error_padding_minutes"), 2),
        ),
    )
    .withColumn(
        "prediction_interval_upper_minutes",
        F.round(F.col("forecast_downtime_minutes") + F.col("prediction_error_padding_minutes"), 2),
    )
    .withColumn("model_name", F.lit(model_name))
    .withColumn("baseline_window_days", F.lit(baseline_window_days))
    .withColumn("forecast_horizon_days", F.lit(forecast_horizon_days))
    .withColumn("min_validation_observations", F.lit(min_validation_observations))
    .withColumn("model_notes", F.lit(model_notes))
    .withColumn("forecast_generated_at", F.current_timestamp())
    .select(
        "forecast_generated_at",
        "forecast_date",
        "latest_actual_date",
        "model_name",
        "baseline_window_days",
        "forecast_horizon_days",
        "min_validation_observations",
        *segment_columns,
        "machine_count",
        "forecast_downtime_minutes",
        "prediction_interval_lower_minutes",
        "prediction_interval_upper_minutes",
        "recent_avg_uptime_pct",
        "recent_avg_health_score",
        "forecast_history_day_count",
        "validation_observation_count",
        "mae_downtime_minutes",
        "rmse_downtime_minutes",
        "mape_pct",
        "backtest_interval_coverage_pct",
        "latest_validation_date",
        "forecast_status",
        "model_notes",
    )
)

if forecast.count() == 0:
    raise ValueError("No downtime forecast rows were generated")

# COMMAND ----------

(
    validation.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(forecast_validation_table)
)

(
    forecast.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(forecast_table)
)

display(forecast.orderBy("forecast_status", F.col("forecast_downtime_minutes").desc()))
