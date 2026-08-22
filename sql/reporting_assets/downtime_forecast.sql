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
