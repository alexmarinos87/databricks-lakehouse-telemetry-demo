WITH monthly_uptime AS (
  SELECT
    DATE_TRUNC('month', CAST(event_date AS TIMESTAMP)) AS event_month,
    client_id,
    site_id,
    model,
    COUNT(DISTINCT machine_id) AS observed_machine_count,
    SUM(COALESCE(running_minutes, 0)) AS running_minutes,
    SUM(COALESCE(observed_minutes, 0)) AS observed_minutes,
    SUM(COALESCE(downtime_minutes, 0)) AS attributed_downtime_minutes,
    ROUND(AVG(avg_health_score), 2) AS avg_daily_machine_health_score,
    MAX(downtime_semantics_version) AS downtime_semantics_version
  FROM main.lakehouse_demo.gold_machine_uptime
  GROUP BY
    DATE_TRUNC('month', CAST(event_date AS TIMESTAMP)),
    client_id,
    site_id,
    model
),
monthly_failures AS (
  SELECT
    DATE_TRUNC('month', CAST(event_date AS TIMESTAMP)) AS event_month,
    client_id,
    site_id,
    model,
    COUNT(*) AS failure_event_count,
    COUNT(DISTINCT machine_id) AS affected_machine_count,
    SUM(COALESCE(downtime_minutes, 0))
      AS failure_attributed_downtime_minutes,
    ROUND(SUM(COALESCE(maintenance_cost_gbp, 0)), 2)
      AS failure_related_cost_gbp
  FROM main.lakehouse_demo.gold_failure_events
  GROUP BY
    DATE_TRUNC('month', CAST(event_date AS TIMESTAMP)),
    client_id,
    site_id,
    model
),
monthly_keys AS (
  SELECT event_month, client_id, site_id, model
  FROM monthly_uptime
  UNION
  SELECT event_month, client_id, site_id, model
  FROM monthly_failures
),
monthly_combined AS (
  SELECT
    keys.event_month,
    keys.client_id,
    keys.site_id,
    keys.model,
    COALESCE(uptime.observed_machine_count, 0) AS observed_machine_count,
    ROUND(COALESCE(uptime.running_minutes, 0) / 60.0, 2)
      AS operating_hours,
    ROUND(COALESCE(uptime.observed_minutes, 0) / 60.0, 2)
      AS observed_hours,
    CASE
      WHEN COALESCE(uptime.observed_minutes, 0) > 0 THEN ROUND(
        uptime.running_minutes / uptime.observed_minutes * 100,
        2
      )
      ELSE NULL
    END AS weighted_uptime_pct,
    uptime.avg_daily_machine_health_score,
    COALESCE(uptime.attributed_downtime_minutes, 0)
      AS attributed_downtime_minutes,
    uptime.downtime_semantics_version,
    COALESCE(failures.failure_event_count, 0) AS failure_event_count,
    COALESCE(failures.affected_machine_count, 0) AS affected_machine_count,
    COALESCE(failures.failure_attributed_downtime_minutes, 0)
      AS failure_attributed_downtime_minutes,
    COALESCE(failures.failure_related_cost_gbp, 0.0)
      AS failure_related_cost_gbp
  FROM monthly_keys AS keys
  LEFT JOIN monthly_uptime AS uptime
    ON keys.event_month = uptime.event_month
    AND keys.client_id = uptime.client_id
    AND keys.site_id = uptime.site_id
    AND keys.model <=> uptime.model
  LEFT JOIN monthly_failures AS failures
    ON keys.event_month = failures.event_month
    AND keys.client_id = failures.client_id
    AND keys.site_id = failures.site_id
    AND keys.model <=> failures.model
),
with_prior_month AS (
  SELECT
    monthly_combined.*,
    LAG(event_month) OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY event_month
    ) AS previous_event_month,
    LAG(weighted_uptime_pct) OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY event_month
    ) AS previous_weighted_uptime_pct,
    LAG(failure_event_count) OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY event_month
    ) AS previous_failure_event_count,
    LAG(attributed_downtime_minutes) OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY event_month
    ) AS previous_attributed_downtime_minutes,
    LAG(failure_related_cost_gbp) OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY event_month
    ) AS previous_failure_related_cost_gbp
  FROM monthly_combined
)
SELECT
  event_month,
  client_id,
  site_id,
  model,
  observed_machine_count,
  operating_hours,
  observed_hours,
  weighted_uptime_pct,
  avg_daily_machine_health_score,
  attributed_downtime_minutes,
  downtime_semantics_version,
  failure_event_count,
  affected_machine_count,
  failure_attributed_downtime_minutes,
  failure_related_cost_gbp,
  previous_event_month,
  CASE
    WHEN observed_hours <= 0 THEN 'no_observed_duration'
    WHEN previous_event_month IS NULL THEN 'first_observed_month'
    WHEN ADD_MONTHS(CAST(previous_event_month AS DATE), 1)
      <> CAST(event_month AS DATE) THEN 'non_consecutive_history'
    ELSE 'consecutive_comparison'
  END AS trend_status,
  CASE
    WHEN previous_event_month IS NOT NULL
      AND ADD_MONTHS(CAST(previous_event_month AS DATE), 1)
        = CAST(event_month AS DATE)
      AND weighted_uptime_pct IS NOT NULL
      AND previous_weighted_uptime_pct IS NOT NULL
    THEN ROUND(weighted_uptime_pct - previous_weighted_uptime_pct, 2)
    ELSE NULL
  END AS weighted_uptime_pct_change,
  CASE
    WHEN previous_event_month IS NOT NULL
      AND ADD_MONTHS(CAST(previous_event_month AS DATE), 1)
        = CAST(event_month AS DATE)
    THEN failure_event_count - previous_failure_event_count
    ELSE NULL
  END AS failure_event_count_change,
  CASE
    WHEN previous_event_month IS NOT NULL
      AND ADD_MONTHS(CAST(previous_event_month AS DATE), 1)
        = CAST(event_month AS DATE)
    THEN attributed_downtime_minutes
      - previous_attributed_downtime_minutes
    ELSE NULL
  END AS attributed_downtime_minutes_change,
  CASE
    WHEN previous_event_month IS NOT NULL
      AND ADD_MONTHS(CAST(previous_event_month AS DATE), 1)
        = CAST(event_month AS DATE)
    THEN ROUND(
      failure_related_cost_gbp - previous_failure_related_cost_gbp,
      2
    )
    ELSE NULL
  END AS failure_related_cost_change_gbp
FROM with_prior_month
ORDER BY
  event_month DESC,
  failure_event_count DESC,
  attributed_downtime_minutes DESC,
  client_id,
  site_id,
  model;
