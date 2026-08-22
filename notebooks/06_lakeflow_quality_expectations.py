# Databricks notebook source
# MAGIC %md
# MAGIC # 06 - Lakeflow quality expectations
# MAGIC
# MAGIC Define a declarative quality-expectations pipeline over the trusted lakehouse outputs.
# MAGIC
# MAGIC This notebook is intended to run inside a Lakeflow Spark Declarative Pipelines
# MAGIC pipeline, not as a standalone notebook task.

# COMMAND ----------

from pyspark import pipelines as dp

# COMMAND ----------

source_catalog = spark.conf.get("lakehouse_demo.catalog", "main")
source_schema = spark.conf.get("lakehouse_demo.schema", "lakehouse_demo")


def source_table(table_name):
    return spark.read.table(f"{source_catalog}.{source_schema}.{table_name}")


# COMMAND ----------

@dp.materialized_view(
    name="quality_expectation_silver_machine_events",
    comment="Silver machine events with pipeline expectation metrics.",
)
@dp.expect_all(
    {
        "event_id_present": "event_id IS NOT NULL AND length(trim(cast(event_id AS STRING))) > 0",
        "machine_id_present": "machine_id IS NOT NULL AND length(trim(cast(machine_id AS STRING))) > 0",
        "site_id_present": "site_id IS NOT NULL AND length(trim(cast(site_id AS STRING))) > 0",
        "client_id_present": "client_id IS NOT NULL AND length(trim(cast(client_id AS STRING))) > 0",
        "event_timestamp_present": "event_ts_utc IS NOT NULL",
        "duration_non_negative": "duration_minutes IS NULL OR duration_minutes >= 0",
        "downtime_non_negative": "downtime_minutes IS NULL OR downtime_minutes >= 0",
        "maintenance_cost_non_negative": "maintenance_cost_gbp IS NULL OR maintenance_cost_gbp >= 0",
        "fuel_level_in_range": "fuel_level_pct IS NULL OR (fuel_level_pct >= 0 AND fuel_level_pct <= 100)",
        "health_score_in_range": "health_score IS NULL OR (health_score >= 0 AND health_score <= 100)",
    }
)
def quality_expectation_silver_machine_events():
    return source_table("silver_machine_events").select(
        "event_id",
        "machine_id",
        "event_ts_utc",
        "event_date",
        "site_id",
        "client_id",
        "model",
        "status",
        "duration_minutes",
        "downtime_minutes",
        "maintenance_cost_gbp",
        "fuel_level_pct",
        "health_score",
        "is_failure_event",
    )


# COMMAND ----------

@dp.materialized_view(
    name="quality_expectation_gold_machine_uptime",
    comment="Gold uptime output with pipeline expectation metrics.",
)
@dp.expect_all(
    {
        "event_date_present": "event_date IS NOT NULL",
        "machine_id_present": "machine_id IS NOT NULL AND length(trim(cast(machine_id AS STRING))) > 0",
        "site_id_present": "site_id IS NOT NULL AND length(trim(cast(site_id AS STRING))) > 0",
        "downtime_non_negative": "downtime_minutes IS NULL OR downtime_minutes >= 0",
        "observed_minutes_non_negative": "observed_minutes IS NULL OR observed_minutes >= 0",
        "uptime_pct_in_range": "uptime_pct IS NULL OR (uptime_pct >= 0 AND uptime_pct <= 100)",
        "avg_health_score_in_range": "avg_health_score IS NULL OR (avg_health_score >= 0 AND avg_health_score <= 100)",
    }
)
def quality_expectation_gold_machine_uptime():
    return source_table("gold_machine_uptime").select(
        "event_date",
        "site_id",
        "client_id",
        "machine_id",
        "model",
        "running_minutes",
        "idle_minutes",
        "maintenance_minutes",
        "downtime_minutes",
        "observed_minutes",
        "avg_health_score",
        "uptime_pct",
    )


# COMMAND ----------

@dp.materialized_view(
    name="quality_expectation_downtime_forecast",
    comment="Downtime forecast output with pipeline expectation metrics.",
)
@dp.expect_all(
    {
        "forecast_run_id_present": (
            "forecast_run_id IS NOT NULL "
            "AND length(trim(cast(forecast_run_id AS STRING))) > 0"
        ),
        "forecast_date_present": "forecast_date IS NOT NULL",
        "segment_keys_present": (
            "site_id IS NOT NULL AND client_id IS NOT NULL AND model IS NOT NULL "
            "AND length(trim(cast(site_id AS STRING))) > 0 "
            "AND length(trim(cast(client_id AS STRING))) > 0 "
            "AND length(trim(cast(model AS STRING))) > 0"
        ),
        "calendar_window_semantics_known": "window_semantics = 'calendar_days'",
        "forecast_non_negative": "forecast_downtime_minutes IS NULL OR forecast_downtime_minutes >= 0",
        "interval_lower_non_negative": (
            "prediction_interval_lower_minutes IS NULL OR prediction_interval_lower_minutes >= 0"
        ),
        "interval_order_valid": (
            "prediction_interval_lower_minutes IS NULL "
            "OR prediction_interval_upper_minutes IS NULL "
            "OR prediction_interval_lower_minutes <= prediction_interval_upper_minutes"
        ),
        "forecast_inside_interval": (
            "forecast_downtime_minutes IS NULL "
            "OR prediction_interval_lower_minutes IS NULL "
            "OR prediction_interval_upper_minutes IS NULL "
            "OR forecast_downtime_minutes BETWEEN prediction_interval_lower_minutes "
            "AND prediction_interval_upper_minutes"
        ),
        "validation_count_non_negative": (
            "validation_observation_count IS NULL "
            "OR validation_observation_count >= 0"
        ),
        "threshold_pair_consistent": (
            "(thresholds_configured = false "
            "AND max_mae_downtime_minutes IS NULL "
            "AND min_interval_coverage_pct IS NULL) "
            "OR (thresholds_configured = true "
            "AND max_mae_downtime_minutes IS NOT NULL "
            "AND min_interval_coverage_pct IS NOT NULL)"
        ),
        "mae_threshold_non_negative": (
            "max_mae_downtime_minutes IS NULL "
            "OR max_mae_downtime_minutes >= 0"
        ),
        "coverage_threshold_in_range": (
            "min_interval_coverage_pct IS NULL "
            "OR min_interval_coverage_pct BETWEEN 0 AND 100"
        ),
        "forecast_status_known": (
            "forecast_status IN ("
            "'validated_baseline', "
            "'insufficient_validation_history', "
            "'thresholds_not_configured', "
            "'accuracy_threshold_failed'"
            ")"
        ),
        "validated_status_is_evidenced": (
            "forecast_status <> 'validated_baseline' "
            "OR (thresholds_configured = true "
            "AND meets_min_validation_samples = true "
            "AND meets_mae_threshold = true "
            "AND meets_interval_coverage_threshold = true)"
        ),
    }
)
def quality_expectation_downtime_forecast():
    return source_table("gold_downtime_forecast").select(
        "forecast_run_id",
        "forecast_generated_at",
        "forecast_date",
        "latest_actual_date",
        "site_id",
        "client_id",
        "model",
        "window_semantics",
        "machine_count",
        "forecast_downtime_minutes",
        "prediction_interval_lower_minutes",
        "prediction_interval_upper_minutes",
        "validation_observation_count",
        "mae_downtime_minutes",
        "rmse_downtime_minutes",
        "backtest_interval_coverage_pct",
        "thresholds_configured",
        "max_mae_downtime_minutes",
        "min_interval_coverage_pct",
        "meets_min_validation_samples",
        "meets_mae_threshold",
        "meets_interval_coverage_threshold",
        "forecast_status",
    )
