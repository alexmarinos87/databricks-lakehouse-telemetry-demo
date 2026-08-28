WITH fault_impact AS (
  SELECT
    DATE_TRUNC('month', CAST(date_dim.date_day AS TIMESTAMP)) AS event_month,
    client_dim.client_id,
    site_dim.site_id,
    model_dim.model,
    fault_dim.fault_code,
    fault_dim.severity,
    fault_dim.severity_rank,
    SUM(failure_fact.failure_event_count) AS failure_event_count,
    COUNT(DISTINCT failure_fact.machine_key) AS affected_machine_count,
    SUM(COALESCE(failure_fact.downtime_minutes, 0))
      AS attributed_downtime_minutes,
    ROUND(SUM(COALESCE(failure_fact.maintenance_cost_gbp, 0)), 2)
      AS maintenance_cost_gbp
  FROM main.lakehouse_demo.fact_machine_failure_event AS failure_fact
  INNER JOIN main.lakehouse_demo.dim_date AS date_dim
    ON failure_fact.date_key = date_dim.date_key
  INNER JOIN main.lakehouse_demo.dim_client AS client_dim
    ON failure_fact.client_key = client_dim.client_key
  INNER JOIN main.lakehouse_demo.dim_site AS site_dim
    ON failure_fact.site_key = site_dim.site_key
  INNER JOIN main.lakehouse_demo.dim_model AS model_dim
    ON failure_fact.model_key = model_dim.model_key
  INNER JOIN main.lakehouse_demo.dim_fault AS fault_dim
    ON failure_fact.fault_key = fault_dim.fault_key
  GROUP BY
    DATE_TRUNC('month', CAST(date_dim.date_day AS TIMESTAMP)),
    client_dim.client_id,
    site_dim.site_id,
    model_dim.model,
    fault_dim.fault_code,
    fault_dim.severity,
    fault_dim.severity_rank
),
ranked_faults AS (
  SELECT
    fault_impact.*,
    SUM(failure_event_count) OVER (
      PARTITION BY event_month, client_id, site_id, model
    ) AS total_failure_event_count,
    SUM(attributed_downtime_minutes) OVER (
      PARTITION BY event_month, client_id, site_id, model
    ) AS total_attributed_downtime_minutes,
    ROUND(
      SUM(maintenance_cost_gbp) OVER (
        PARTITION BY event_month, client_id, site_id, model
      ),
      2
    ) AS total_maintenance_cost_gbp,
    ROW_NUMBER() OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        failure_event_count DESC,
        attributed_downtime_minutes DESC,
        maintenance_cost_gbp DESC,
        severity_rank DESC,
        fault_code
    ) AS fault_impact_rank,
    SUM(failure_event_count) OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        failure_event_count DESC,
        attributed_downtime_minutes DESC,
        maintenance_cost_gbp DESC,
        severity_rank DESC,
        fault_code
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_failure_event_count,
    SUM(attributed_downtime_minutes) OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        failure_event_count DESC,
        attributed_downtime_minutes DESC,
        maintenance_cost_gbp DESC,
        severity_rank DESC,
        fault_code
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_attributed_downtime_minutes,
    ROUND(
      SUM(maintenance_cost_gbp) OVER (
        PARTITION BY event_month, client_id, site_id, model
        ORDER BY
          failure_event_count DESC,
          attributed_downtime_minutes DESC,
          maintenance_cost_gbp DESC,
          severity_rank DESC,
          fault_code
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ),
      2
    ) AS cumulative_maintenance_cost_gbp
  FROM fault_impact
)
SELECT
  event_month,
  client_id,
  site_id,
  model,
  fault_code,
  severity,
  severity_rank,
  fault_impact_rank,
  failure_event_count,
  affected_machine_count,
  attributed_downtime_minutes,
  maintenance_cost_gbp,
  total_failure_event_count,
  total_attributed_downtime_minutes,
  total_maintenance_cost_gbp,
  CASE
    WHEN total_failure_event_count > 0 THEN ROUND(
      failure_event_count / total_failure_event_count * 100,
      2
    )
    ELSE NULL
  END AS failure_event_share_pct,
  CASE
    WHEN total_failure_event_count > 0 THEN ROUND(
      cumulative_failure_event_count / total_failure_event_count * 100,
      2
    )
    ELSE NULL
  END AS cumulative_failure_event_share_pct,
  CASE
    WHEN total_attributed_downtime_minutes > 0 THEN ROUND(
      attributed_downtime_minutes
        / total_attributed_downtime_minutes * 100,
      2
    )
    ELSE NULL
  END AS attributed_downtime_share_pct,
  CASE
    WHEN total_attributed_downtime_minutes > 0 THEN ROUND(
      cumulative_attributed_downtime_minutes
        / total_attributed_downtime_minutes * 100,
      2
    )
    ELSE NULL
  END AS cumulative_attributed_downtime_share_pct,
  CASE
    WHEN total_maintenance_cost_gbp > 0 THEN ROUND(
      maintenance_cost_gbp / total_maintenance_cost_gbp * 100,
      2
    )
    ELSE NULL
  END AS maintenance_cost_share_pct,
  CASE
    WHEN total_maintenance_cost_gbp > 0 THEN ROUND(
      cumulative_maintenance_cost_gbp
        / total_maintenance_cost_gbp * 100,
      2
    )
    ELSE NULL
  END AS cumulative_maintenance_cost_share_pct,
  CASE
    WHEN total_failure_event_count <= 0 THEN 'no_recorded_failures'
    WHEN total_attributed_downtime_minutes <= 0
      AND total_maintenance_cost_gbp <= 0
    THEN 'failures_without_recorded_impact'
    WHEN total_maintenance_cost_gbp <= 0
    THEN 'failures_without_recorded_cost'
    ELSE 'failure_impact_observed'
  END AS impact_evidence_status
FROM ranked_faults
ORDER BY
  event_month DESC,
  client_id,
  site_id,
  model,
  fault_impact_rank;
