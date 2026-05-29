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
