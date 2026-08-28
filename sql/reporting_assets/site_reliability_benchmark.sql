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
    SUM(COALESCE(downtime_minutes, 0)) AS failure_attributed_downtime_minutes,
    SUM(COALESCE(maintenance_cost_gbp, 0)) AS failure_related_cost_gbp
  FROM main.lakehouse_demo.gold_failure_events
  GROUP BY
    DATE_TRUNC('month', CAST(event_date AS TIMESTAMP)),
    client_id,
    site_id,
    model
),
monthly_keys AS (
  SELECT
    event_month,
    client_id,
    site_id,
    model
  FROM monthly_uptime
  UNION
  SELECT
    event_month,
    client_id,
    site_id,
    model
  FROM monthly_failures
),
site_metrics AS (
  SELECT
    keys.event_month,
    keys.client_id,
    keys.site_id,
    keys.model,
    COALESCE(uptime.observed_machine_count, 0) AS observed_machine_count,
    COALESCE(uptime.running_minutes, 0) AS running_minutes,
    COALESCE(uptime.observed_minutes, 0) AS observed_minutes,
    ROUND(COALESCE(uptime.running_minutes, 0) / 60.0, 2) AS operating_hours,
    ROUND(COALESCE(uptime.observed_minutes, 0) / 60.0, 2) AS observed_hours,
    CASE
      WHEN COALESCE(uptime.observed_minutes, 0) > 0
      THEN ROUND(
        uptime.running_minutes / uptime.observed_minutes * 100,
        2
      )
      ELSE NULL
    END AS weighted_uptime_pct,
    COALESCE(uptime.attributed_downtime_minutes, 0)
      AS attributed_downtime_minutes,
    uptime.downtime_semantics_version,
    COALESCE(failures.failure_event_count, 0) AS failure_event_count,
    COALESCE(failures.affected_machine_count, 0) AS affected_machine_count,
    COALESCE(failures.failure_attributed_downtime_minutes, 0)
      AS failure_attributed_downtime_minutes,
    COALESCE(failures.failure_related_cost_gbp, 0.0)
      AS failure_related_cost_gbp,
    CASE
      WHEN COALESCE(uptime.running_minutes, 0) > 0
      THEN ROUND(
        COALESCE(failures.failure_event_count, 0) * 6000.0
          / uptime.running_minutes,
        2
      )
      ELSE NULL
    END AS failure_events_per_100_operating_hours,
    CASE
      WHEN COALESCE(uptime.running_minutes, 0) > 0
      THEN ROUND(
        COALESCE(failures.failure_related_cost_gbp, 0.0) * 60.0
          / uptime.running_minutes,
        2
      )
      ELSE NULL
    END AS failure_related_cost_per_operating_hour_gbp
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
benchmark_inputs AS (
  SELECT
    site_metrics.*,
    SUM(running_minutes) OVER (
      PARTITION BY event_month, client_id, model
    ) AS client_model_running_minutes,
    SUM(observed_minutes) OVER (
      PARTITION BY event_month, client_id, model
    ) AS client_model_observed_minutes,
    SUM(failure_event_count) OVER (
      PARTITION BY event_month, client_id, model
    ) AS client_model_failure_event_count,
    SUM(failure_related_cost_gbp) OVER (
      PARTITION BY event_month, client_id, model
    ) AS client_model_failure_related_cost_gbp,
    COUNT(*) OVER (
      PARTITION BY event_month, client_id, model
    ) AS benchmark_site_row_count,
    SUM(
      CASE
        WHEN observed_minutes > 0 THEN 1
        ELSE 0
      END
    ) OVER (
      PARTITION BY event_month, client_id, model
    ) AS comparable_observed_site_count,
    SUM(
      CASE
        WHEN running_minutes > 0 THEN 1
        ELSE 0
      END
    ) OVER (
      PARTITION BY event_month, client_id, model
    ) AS comparable_operating_site_count
  FROM site_metrics
),
with_benchmark_rates AS (
  SELECT
    benchmark_inputs.*,
    CASE
      WHEN client_model_observed_minutes > 0
      THEN ROUND(
        client_model_running_minutes / client_model_observed_minutes * 100,
        2
      )
      ELSE NULL
    END AS client_model_weighted_uptime_pct,
    CASE
      WHEN client_model_running_minutes > 0
      THEN ROUND(
        client_model_failure_event_count * 6000.0
          / client_model_running_minutes,
        2
      )
      ELSE NULL
    END AS client_model_failure_events_per_100_operating_hours,
    CASE
      WHEN client_model_running_minutes > 0
      THEN ROUND(
        client_model_failure_related_cost_gbp * 60.0
          / client_model_running_minutes,
        2
      )
      ELSE NULL
    END AS client_model_failure_related_cost_per_operating_hour_gbp
  FROM benchmark_inputs
),
with_benchmark_deltas AS (
  SELECT
    with_benchmark_rates.*,
    CASE
      WHEN weighted_uptime_pct IS NOT NULL
        AND client_model_weighted_uptime_pct IS NOT NULL
      THEN ROUND(
        weighted_uptime_pct - client_model_weighted_uptime_pct,
        2
      )
      ELSE NULL
    END AS weighted_uptime_pct_vs_client_model,
    CASE
      WHEN failure_events_per_100_operating_hours IS NOT NULL
        AND client_model_failure_events_per_100_operating_hours IS NOT NULL
      THEN ROUND(
        failure_events_per_100_operating_hours
          - client_model_failure_events_per_100_operating_hours,
        2
      )
      ELSE NULL
    END AS failure_rate_vs_client_model,
    CASE
      WHEN failure_related_cost_per_operating_hour_gbp IS NOT NULL
        AND client_model_failure_related_cost_per_operating_hour_gbp IS NOT NULL
      THEN ROUND(
        failure_related_cost_per_operating_hour_gbp
          - client_model_failure_related_cost_per_operating_hour_gbp,
        2
      )
      ELSE NULL
    END AS cost_rate_vs_client_model_gbp
  FROM with_benchmark_rates
),
ranked_sites AS (
  SELECT
    with_benchmark_deltas.*,
    ROW_NUMBER() OVER (
      PARTITION BY event_month, client_id, model
      ORDER BY
        weighted_uptime_pct DESC NULLS LAST,
        failure_events_per_100_operating_hours ASC NULLS LAST,
        failure_related_cost_per_operating_hour_gbp ASC NULLS LAST,
        site_id
    ) AS site_comparison_rank
  FROM with_benchmark_deltas
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
  client_model_weighted_uptime_pct,
  weighted_uptime_pct_vs_client_model,
  failure_event_count,
  affected_machine_count,
  failure_events_per_100_operating_hours,
  client_model_failure_events_per_100_operating_hours,
  failure_rate_vs_client_model,
  ROUND(failure_related_cost_gbp, 2) AS failure_related_cost_gbp,
  failure_related_cost_per_operating_hour_gbp,
  client_model_failure_related_cost_per_operating_hour_gbp,
  cost_rate_vs_client_model_gbp,
  attributed_downtime_minutes,
  failure_attributed_downtime_minutes,
  downtime_semantics_version,
  benchmark_site_row_count,
  comparable_observed_site_count,
  comparable_operating_site_count,
  site_comparison_rank,
  CASE
    WHEN observed_minutes <= 0 THEN 'site_has_no_observed_duration'
    WHEN running_minutes <= 0 THEN 'site_has_no_operating_time'
    WHEN comparable_observed_site_count < 2
      THEN 'single_observed_site_reference'
    ELSE 'multi_site_comparison'
  END AS benchmark_status
FROM ranked_sites
ORDER BY
  event_month DESC,
  client_id,
  model,
  site_comparison_rank,
  site_id;
