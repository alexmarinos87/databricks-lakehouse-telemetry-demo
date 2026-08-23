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
    comment="Gold uptime output with attributed-downtime semantic evidence.",
)
@dp.expect_all(
    {
        "event_date_present": "event_date IS NOT NULL",
        "machine_id_present": "machine_id IS NOT NULL AND length(trim(cast(machine_id AS STRING))) > 0",
        "site_id_present": "site_id IS NOT NULL AND length(trim(cast(site_id AS STRING))) > 0",
        "duration_values_present": (
            "running_minutes IS NOT NULL AND idle_minutes IS NOT NULL "
            "AND maintenance_minutes IS NOT NULL "
            "AND downtime_minutes IS NOT NULL AND observed_minutes IS NOT NULL"
        ),
        "duration_values_non_negative": (
            "running_minutes >= 0 AND idle_minutes >= 0 "
            "AND maintenance_minutes >= 0 "
            "AND downtime_minutes >= 0 AND observed_minutes >= 0"
        ),
        "status_minutes_within_observed": (
            "coalesce(running_minutes, 0) + coalesce(idle_minutes, 0) "
            "+ coalesce(maintenance_minutes, 0) <= coalesce(observed_minutes, 0)"
        ),
        "uptime_pct_in_range": "uptime_pct IS NULL OR (uptime_pct >= 0 AND uptime_pct <= 100)",
        "avg_health_score_in_range": "avg_health_score IS NULL OR (avg_health_score >= 0 AND avg_health_score <= 100)",
        "downtime_load_formula_valid": (
            "(observed_minutes > 0 "
            "AND downtime_load_pct IS NOT NULL "
            "AND abs(downtime_load_pct "
            "- round(downtime_minutes / observed_minutes * 100, 2)) <= 0.01) "
            "OR (observed_minutes = 0 AND downtime_minutes = 0 "
            "AND downtime_load_pct = 0) "
            "OR (observed_minutes = 0 AND downtime_minutes > 0 "
            "AND downtime_load_pct IS NULL)"
        ),
        "downtime_compatibility_alias_consistent": (
            "downtime_pct <=> downtime_load_pct"
        ),
        "downtime_exceedance_flag_consistent": (
            "downtime_exceeds_observed "
            "<=> (downtime_minutes > observed_minutes)"
        ),
        "downtime_semantics_version_known": (
            "downtime_semantics_version = 'attributed_incident_v1'"
        ),
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
        "downtime_pct",
        "downtime_load_pct",
        "downtime_exceeds_observed",
        "downtime_semantics_version",
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
        "publication_committed": (
            "publication_completed_at_utc IS NOT NULL "
            "AND length(publication_completed_at_utc) = 20"
        ),
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
        "publication_completed_at_utc",
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


# COMMAND ----------

@dp.materialized_view(
    name="quality_expectation_forecast_publication_manifest",
    comment="Forecast publication runs with commit-boundary evidence.",
)
@dp.expect_all(
    {
        "forecast_run_id_present": (
            "forecast_run_id IS NOT NULL "
            "AND length(trim(cast(forecast_run_id AS STRING))) > 0"
        ),
        "publication_state_known": (
            "publication_state IN ('STARTED', 'COMMITTED', 'FAILED')"
        ),
        "publication_started_at_present": (
            "publication_started_at_utc IS NOT NULL "
            "AND length(publication_started_at_utc) = 20"
        ),
        "row_counts_non_negative": (
            "forecast_row_count >= 0 AND validation_row_count >= 0"
        ),
        "fingerprints_sha256_shaped": (
            "length(forecast_schema_sha256) = 64 "
            "AND length(validation_schema_sha256) = 64 "
            "AND length(forecast_payload_sha256) = 64 "
            "AND length(validation_payload_sha256) = 64"
        ),
        "committed_evidence_complete": (
            "publication_state <> 'COMMITTED' "
            "OR (publication_completed_at_utc IS NOT NULL "
            "AND length(publication_completed_at_utc) = 20 "
            "AND forecast_columns_json IS NOT NULL "
            "AND validation_columns_json IS NOT NULL)"
        ),
    }
)
def quality_expectation_forecast_publication_manifest():
    return source_table("gold_downtime_forecast_publication_manifest").select(
        "forecast_run_id",
        "publication_state",
        "publication_started_at_utc",
        "publication_completed_at_utc",
        "forecast_generated_at_utc",
        "model_name",
        "window_semantics",
        "baseline_window_days",
        "forecast_row_count",
        "validation_row_count",
        "forecast_columns_json",
        "validation_columns_json",
        "forecast_schema_sha256",
        "validation_schema_sha256",
        "forecast_payload_sha256",
        "validation_payload_sha256",
    )
