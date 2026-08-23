SELECT
  event_date,
  site_id,
  model,
  ROUND(AVG(uptime_pct), 2) AS avg_uptime_pct,
  SUM(downtime_minutes) AS attributed_downtime_minutes,
  ROUND(AVG(downtime_load_pct), 2) AS avg_downtime_load_pct,
  SUM(CASE WHEN downtime_exceeds_observed THEN 1 ELSE 0 END)
    AS attributed_downtime_above_observation_count,
  MAX(downtime_semantics_version) AS downtime_semantics_version,
  ROUND(AVG(avg_health_score), 2) AS avg_health_score
FROM main.lakehouse_demo.gold_machine_uptime
GROUP BY event_date, site_id, model
ORDER BY event_date, site_id, model;
