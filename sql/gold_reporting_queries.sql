-- BI-ready reporting queries for the Databricks Lakehouse demo.
-- Replace `main.lakehouse_demo` if you run the notebooks with different widget values.

-- Daily uptime by site and model
SELECT
  event_date,
  site_id,
  model,
  ROUND(AVG(uptime_pct), 2) AS avg_uptime_pct,
  SUM(downtime_minutes) AS downtime_minutes,
  ROUND(AVG(avg_health_score), 2) AS avg_health_score
FROM main.lakehouse_demo.gold_machine_uptime
GROUP BY event_date, site_id, model
ORDER BY event_date, site_id, model;

-- Highest cost failure events
SELECT
  event_date,
  site_id,
  client_id,
  machine_id,
  model,
  fault_code,
  severity,
  downtime_minutes,
  maintenance_cost_gbp
FROM main.lakehouse_demo.gold_failure_events
ORDER BY maintenance_cost_gbp DESC, downtime_minutes DESC;

-- Maintenance cost by month and model
SELECT
  event_month,
  site_id,
  model,
  SUM(maintenance_event_count) AS maintenance_events,
  SUM(failure_event_count) AS failure_events,
  ROUND(SUM(maintenance_cost_gbp), 2) AS maintenance_cost_gbp,
  SUM(downtime_minutes) AS downtime_minutes
FROM main.lakehouse_demo.gold_maintenance_costs
GROUP BY event_month, site_id, model
ORDER BY event_month, maintenance_cost_gbp DESC;

-- Parts usage by site
SELECT
  event_date,
  site_id,
  part_code,
  SUM(part_quantity) AS part_quantity,
  ROUND(SUM(associated_cost_gbp), 2) AS associated_cost_gbp,
  SUM(machine_count) AS impacted_machine_count
FROM main.lakehouse_demo.gold_parts_usage
GROUP BY event_date, site_id, part_code
ORDER BY event_date, associated_cost_gbp DESC;

-- Client asset summary for account and service reviews
SELECT
  client_id,
  site_id,
  machine_id,
  model,
  avg_uptime_pct,
  avg_health_score,
  total_downtime_minutes,
  failure_event_count,
  failure_related_cost_gbp
FROM main.lakehouse_demo.gold_client_asset_summary
ORDER BY client_id, site_id, failure_related_cost_gbp DESC;

-- Downtime forecast with explicit client-readiness evidence
SELECT
  forecast_run_id,
  forecast_generated_at,
  forecast_date,
  latest_actual_date,
  site_id,
  client_id,
  model,
  window_semantics,
  baseline_window_days,
  forecast_horizon_days,
  machine_count,
  forecast_downtime_minutes,
  prediction_interval_lower_minutes,
  prediction_interval_upper_minutes,
  validation_observation_count,
  mae_downtime_minutes,
  rmse_downtime_minutes,
  backtest_interval_coverage_pct,
  thresholds_configured,
  max_mae_downtime_minutes,
  min_interval_coverage_pct,
  meets_min_validation_samples,
  meets_mae_threshold,
  meets_interval_coverage_threshold,
  forecast_status
FROM main.lakehouse_demo.gold_downtime_forecast
ORDER BY
  CASE forecast_status
    WHEN 'validated_baseline' THEN 1
    WHEN 'accuracy_threshold_failed' THEN 2
    WHEN 'thresholds_not_configured' THEN 3
    ELSE 4
  END,
  forecast_downtime_minutes DESC;

-- Downtime forecast backtest performance for the current published run
SELECT
  forecast_run_id,
  validation_generated_at,
  site_id,
  client_id,
  model,
  window_semantics,
  baseline_window_days,
  COUNT(*) AS validation_observation_count,
  ROUND(AVG(absolute_error_minutes), 2) AS mae_downtime_minutes,
  ROUND(SQRT(AVG(squared_error_minutes)), 2) AS rmse_downtime_minutes,
  ROUND(AVG(absolute_percentage_error) * 100, 2) AS mape_pct,
  ROUND(
    AVG(CASE WHEN covered_by_validation_interval THEN 1.0 ELSE 0.0 END) * 100,
    2
  ) AS backtest_interval_coverage_pct,
  MAX(event_date) AS latest_validation_date
FROM main.lakehouse_demo.gold_downtime_forecast_validation
GROUP BY
  forecast_run_id,
  validation_generated_at,
  site_id,
  client_id,
  model,
  window_semantics,
  baseline_window_days
ORDER BY mae_downtime_minutes DESC, site_id, model;

-- Latest failed quality checks plus the durable run-level outcome
WITH latest_quality_run AS (
  SELECT
    quality_run_id,
    checked_at,
    failed_error_check_count,
    failed_warning_check_count,
    all_error_checks_passed
  FROM main.lakehouse_demo.quality_metric_history
  ORDER BY checked_at DESC
  LIMIT 1
)
SELECT
  latest.quality_run_id,
  latest.checked_at,
  latest.failed_error_check_count,
  latest.failed_warning_check_count,
  latest.all_error_checks_passed,
  q.severity,
  q.check_name,
  q.status,
  q.detail,
  q.observed_count
FROM latest_quality_run AS latest
LEFT JOIN main.lakehouse_demo.quality_check_results AS q
  ON q.quality_run_id = latest.quality_run_id
  AND q.status = 'fail'
ORDER BY
  CASE q.severity
    WHEN 'error' THEN 1
    WHEN 'warning' THEN 2
    ELSE 3
  END,
  q.check_name;

-- Recent quality check run history for monitoring trends
SELECT
  quality_run_id,
  checked_at,
  check_count,
  passed_check_count,
  failed_check_count,
  failed_error_check_count,
  failed_warning_check_count,
  all_error_checks_passed
FROM main.lakehouse_demo.quality_metric_history
ORDER BY checked_at DESC
LIMIT 30;

-- Recent Lakeflow expectation pipeline events
SELECT
  timestamp,
  event_type,
  level,
  message,
  details
FROM main.lakehouse_demo.quality_expectation_event_log
WHERE event_type IN ('flow_progress', 'update_progress')
ORDER BY timestamp DESC
LIMIT 50;

-- Row counts from expectation-backed materialized views
SELECT
  'quality_expectation_silver_machine_events' AS expectation_dataset,
  COUNT(*) AS row_count
FROM main.lakehouse_demo.quality_expectation_silver_machine_events
UNION ALL
SELECT
  'quality_expectation_gold_machine_uptime' AS expectation_dataset,
  COUNT(*) AS row_count
FROM main.lakehouse_demo.quality_expectation_gold_machine_uptime
UNION ALL
SELECT
  'quality_expectation_downtime_forecast' AS expectation_dataset,
  COUNT(*) AS row_count
FROM main.lakehouse_demo.quality_expectation_downtime_forecast
ORDER BY expectation_dataset;
