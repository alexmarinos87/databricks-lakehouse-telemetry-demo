SELECT
  forecast_date,
  site_id,
  client_id,
  model,
  machine_count,
  forecast_downtime_minutes,
  prediction_interval_lower_minutes,
  prediction_interval_upper_minutes,
  validation_observation_count,
  mae_downtime_minutes,
  rmse_downtime_minutes,
  backtest_interval_coverage_pct,
  forecast_status
FROM main.lakehouse_demo.gold_downtime_forecast
ORDER BY
  CASE forecast_status
    WHEN 'validated_baseline' THEN 1
    ELSE 2
  END,
  forecast_downtime_minutes DESC;
