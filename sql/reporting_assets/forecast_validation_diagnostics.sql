WITH validation_rows AS (
  SELECT
    forecast_run_id,
    validation_generated_at,
    publication_completed_at_utc,
    model_name,
    window_semantics,
    baseline_window_days,
    event_date,
    client_id,
    site_id,
    model,
    machine_count,
    actual_downtime_minutes,
    forecast_downtime_minutes,
    history_day_count,
    history_start_date,
    history_calendar_span_days,
    absolute_error_minutes,
    squared_error_minutes,
    absolute_percentage_error,
    residual_minutes,
    validation_interval_lower_minutes,
    validation_interval_upper_minutes,
    covered_by_validation_interval,
    ROW_NUMBER() OVER (
      PARTITION BY
        forecast_run_id,
        client_id,
        site_id,
        model
      ORDER BY
        absolute_error_minutes DESC,
        event_date DESC
    ) AS absolute_error_rank
  FROM main.lakehouse_demo.gold_downtime_forecast_validation
),
segment_metrics AS (
  SELECT
    forecast_run_id,
    MAX(validation_generated_at) AS validation_generated_at,
    MAX(publication_completed_at_utc) AS publication_completed_at_utc,
    model_name,
    window_semantics,
    baseline_window_days,
    client_id,
    site_id,
    model,
    COUNT(*) AS validation_observation_count,
    MIN(event_date) AS first_validation_date,
    MAX(event_date) AS latest_validation_date,
    ROUND(AVG(actual_downtime_minutes), 2) AS avg_actual_downtime_minutes,
    ROUND(AVG(forecast_downtime_minutes), 2) AS avg_forecast_downtime_minutes,
    ROUND(AVG(residual_minutes), 2) AS mean_error_minutes,
    ROUND(AVG(absolute_error_minutes), 2) AS mae_downtime_minutes,
    ROUND(SQRT(AVG(squared_error_minutes)), 2) AS rmse_downtime_minutes,
    SUM(
      CASE
        WHEN absolute_percentage_error IS NOT NULL THEN 1
        ELSE 0
      END
    ) AS percentage_error_observation_count,
    CASE
      WHEN SUM(
        CASE
          WHEN absolute_percentage_error IS NOT NULL THEN 1
          ELSE 0
        END
      ) > 0
      THEN ROUND(AVG(absolute_percentage_error) * 100, 2)
      ELSE NULL
    END AS mape_pct,
    CASE
      WHEN COUNT(*) > 0
      THEN ROUND(
        AVG(
          CASE
            WHEN covered_by_validation_interval THEN 1.0
            ELSE 0.0
          END
        ) * 100,
        2
      )
      ELSE NULL
    END AS validation_interval_coverage_pct,
    SUM(
      CASE
        WHEN residual_minutes > 0 THEN 1
        ELSE 0
      END
    ) AS under_forecast_observation_count,
    SUM(
      CASE
        WHEN residual_minutes < 0 THEN 1
        ELSE 0
      END
    ) AS over_forecast_observation_count,
    SUM(
      CASE
        WHEN residual_minutes = 0 THEN 1
        ELSE 0
      END
    ) AS exact_forecast_observation_count
  FROM validation_rows
  GROUP BY
    forecast_run_id,
    model_name,
    window_semantics,
    baseline_window_days,
    client_id,
    site_id,
    model
),
largest_error_observation AS (
  SELECT
    forecast_run_id,
    client_id,
    site_id,
    model,
    event_date AS largest_absolute_error_date,
    machine_count AS largest_error_machine_count,
    actual_downtime_minutes AS largest_error_actual_downtime_minutes,
    forecast_downtime_minutes AS largest_error_forecast_downtime_minutes,
    absolute_error_minutes AS largest_absolute_error_minutes,
    residual_minutes AS largest_error_residual_minutes,
    history_day_count AS largest_error_history_day_count,
    history_start_date AS largest_error_history_start_date,
    history_calendar_span_days AS largest_error_history_calendar_span_days,
    validation_interval_lower_minutes AS largest_error_interval_lower_minutes,
    validation_interval_upper_minutes AS largest_error_interval_upper_minutes,
    covered_by_validation_interval AS largest_error_covered_by_interval
  FROM validation_rows
  WHERE absolute_error_rank = 1
)
SELECT
  metrics.forecast_run_id,
  metrics.validation_generated_at,
  metrics.publication_completed_at_utc,
  metrics.model_name,
  metrics.window_semantics,
  metrics.baseline_window_days,
  metrics.client_id,
  metrics.site_id,
  metrics.model,
  metrics.validation_observation_count,
  metrics.first_validation_date,
  metrics.latest_validation_date,
  metrics.avg_actual_downtime_minutes,
  metrics.avg_forecast_downtime_minutes,
  metrics.mean_error_minutes,
  metrics.mae_downtime_minutes,
  metrics.rmse_downtime_minutes,
  metrics.percentage_error_observation_count,
  metrics.mape_pct,
  metrics.validation_interval_coverage_pct,
  metrics.under_forecast_observation_count,
  metrics.over_forecast_observation_count,
  metrics.exact_forecast_observation_count,
  CASE
    WHEN metrics.mae_downtime_minutes = 0 THEN 'exact_validation_observations'
    WHEN metrics.mean_error_minutes > 0 THEN 'under_forecast_bias_observed'
    WHEN metrics.mean_error_minutes < 0 THEN 'over_forecast_bias_observed'
    ELSE 'balanced_mean_error'
  END AS validation_bias_status,
  largest.largest_absolute_error_date,
  largest.largest_error_machine_count,
  largest.largest_error_actual_downtime_minutes,
  largest.largest_error_forecast_downtime_minutes,
  largest.largest_absolute_error_minutes,
  largest.largest_error_residual_minutes,
  largest.largest_error_history_day_count,
  largest.largest_error_history_start_date,
  largest.largest_error_history_calendar_span_days,
  largest.largest_error_interval_lower_minutes,
  largest.largest_error_interval_upper_minutes,
  largest.largest_error_covered_by_interval
FROM segment_metrics AS metrics
INNER JOIN largest_error_observation AS largest
  ON metrics.forecast_run_id = largest.forecast_run_id
  AND metrics.client_id = largest.client_id
  AND metrics.site_id = largest.site_id
  AND metrics.model <=> largest.model
ORDER BY
  metrics.mae_downtime_minutes DESC,
  ABS(metrics.mean_error_minutes) DESC,
  metrics.client_id,
  metrics.site_id,
  metrics.model;
