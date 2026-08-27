WITH uptime AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    MIN(event_date) AS first_observed_date,
    MAX(event_date) AS last_observed_date,
    SUM(running_minutes) AS running_minutes,
    SUM(observed_minutes) AS observed_minutes,
    SUM(downtime_minutes) AS attributed_downtime_minutes,
    ROUND(AVG(uptime_pct), 2) AS avg_uptime_pct,
    ROUND(AVG(avg_health_score), 2) AS avg_health_score,
    MAX(downtime_semantics_version) AS downtime_semantics_version
  FROM main.lakehouse_demo.gold_machine_uptime
  GROUP BY client_id, site_id, machine_id, model
),
failures AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    COUNT(*) AS failure_event_count,
    SUM(COALESCE(downtime_minutes, 0))
      AS failure_attributed_downtime_minutes,
    ROUND(SUM(COALESCE(maintenance_cost_gbp, 0)), 2)
      AS failure_related_cost_gbp,
    MAX(event_ts_utc) AS latest_failure_at_utc
  FROM main.lakehouse_demo.gold_failure_events
  GROUP BY client_id, site_id, machine_id, model
)
SELECT
  u.client_id,
  u.site_id,
  u.machine_id,
  u.model,
  u.first_observed_date,
  u.last_observed_date,
  ROUND(u.running_minutes / 60.0, 2) AS operating_hours,
  ROUND(u.observed_minutes / 60.0, 2) AS observed_hours,
  u.avg_uptime_pct,
  u.avg_health_score,
  u.attributed_downtime_minutes,
  COALESCE(f.failure_event_count, 0) AS failure_event_count,
  CASE
    WHEN u.running_minutes > 0 THEN
      ROUND(
        COALESCE(f.failure_event_count, 0)
        / (u.running_minutes / 60.0)
        * 100,
        2
      )
    ELSE NULL
  END AS failures_per_100_operating_hours,
  CASE
    WHEN COALESCE(f.failure_event_count, 0) > 0 THEN
      ROUND(
        f.failure_attributed_downtime_minutes / f.failure_event_count,
        2
      )
    ELSE NULL
  END AS avg_attributed_downtime_per_failure_minutes,
  CASE
    WHEN COALESCE(f.failure_event_count, 0) > 0 THEN
      ROUND(f.failure_related_cost_gbp / f.failure_event_count, 2)
    ELSE NULL
  END AS avg_failure_related_cost_gbp,
  COALESCE(f.failure_related_cost_gbp, 0.0) AS failure_related_cost_gbp,
  f.latest_failure_at_utc,
  CASE
    WHEN u.running_minutes <= 0 THEN 'no_operating_time'
    WHEN COALESCE(f.failure_event_count, 0) = 0 THEN 'no_recorded_failures'
    ELSE 'observed_failures'
  END AS reliability_observation_status,
  u.downtime_semantics_version
FROM uptime AS u
LEFT JOIN failures AS f
  ON u.client_id = f.client_id
 AND u.site_id = f.site_id
 AND u.machine_id = f.machine_id
 AND u.model <=> f.model
ORDER BY
  failures_per_100_operating_hours DESC NULLS LAST,
  failure_related_cost_gbp DESC,
  u.client_id,
  u.site_id,
  u.machine_id;
