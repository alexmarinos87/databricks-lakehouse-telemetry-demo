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
