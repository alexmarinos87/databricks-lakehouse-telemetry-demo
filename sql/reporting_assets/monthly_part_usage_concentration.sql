WITH part_usage AS (
  SELECT
    DATE_TRUNC('month', CAST(date_dim.date_day AS TIMESTAMP)) AS event_month,
    client_dim.client_id,
    site_dim.site_id,
    model_dim.model,
    failure_fact.part_code,
    SUM(failure_fact.failure_event_count) AS observed_part_event_count,
    COUNT(DISTINCT failure_fact.event_id)
      AS observed_part_identity_count,
    COUNT(DISTINCT failure_fact.machine_key)
      AS affected_machine_count,
    COUNT(DISTINCT failure_fact.date_key)
      AS observed_part_day_count,
    COUNT(DISTINCT failure_fact.fault_key)
      AS associated_fault_count,
    SUM(COALESCE(failure_fact.part_quantity, 0))
      AS recorded_part_quantity,
    SUM(COALESCE(failure_fact.downtime_minutes, 0))
      AS associated_attributed_downtime_minutes,
    ROUND(
      SUM(COALESCE(failure_fact.maintenance_cost_gbp, 0)),
      2
    ) AS associated_failure_cost_gbp
  FROM main.lakehouse_demo.fact_machine_failure_event AS failure_fact
  INNER JOIN main.lakehouse_demo.dim_date AS date_dim
    ON failure_fact.date_key = date_dim.date_key
  INNER JOIN main.lakehouse_demo.dim_client AS client_dim
    ON failure_fact.client_key = client_dim.client_key
  INNER JOIN main.lakehouse_demo.dim_site AS site_dim
    ON failure_fact.site_key = site_dim.site_key
  INNER JOIN main.lakehouse_demo.dim_model AS model_dim
    ON failure_fact.model_key = model_dim.model_key
  WHERE failure_fact.part_code IS NOT NULL
    AND UPPER(TRIM(failure_fact.part_code)) <> 'NONE'
    AND COALESCE(failure_fact.part_quantity, 0) > 0
  GROUP BY
    DATE_TRUNC('month', CAST(date_dim.date_day AS TIMESTAMP)),
    client_dim.client_id,
    site_dim.site_id,
    model_dim.model,
    failure_fact.part_code
),
ranked_parts AS (
  SELECT
    part_usage.*,
    COUNT(*) OVER (
      PARTITION BY event_month, client_id, site_id, model
    ) AS recorded_part_count,
    SUM(recorded_part_quantity) OVER (
      PARTITION BY event_month, client_id, site_id, model
    ) AS total_recorded_part_quantity,
    SUM(observed_part_event_count) OVER (
      PARTITION BY event_month, client_id, site_id, model
    ) AS total_observed_part_event_count,
    SUM(associated_attributed_downtime_minutes) OVER (
      PARTITION BY event_month, client_id, site_id, model
    ) AS total_associated_attributed_downtime_minutes,
    ROUND(
      SUM(associated_failure_cost_gbp) OVER (
        PARTITION BY event_month, client_id, site_id, model
      ),
      2
    ) AS total_associated_failure_cost_gbp,
    ROW_NUMBER() OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        recorded_part_quantity DESC,
        observed_part_event_count DESC,
        associated_failure_cost_gbp DESC,
        associated_attributed_downtime_minutes DESC,
        part_code
    ) AS part_usage_rank,
    SUM(recorded_part_quantity) OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        recorded_part_quantity DESC,
        observed_part_event_count DESC,
        associated_failure_cost_gbp DESC,
        associated_attributed_downtime_minutes DESC,
        part_code
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_recorded_part_quantity,
    SUM(observed_part_event_count) OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        recorded_part_quantity DESC,
        observed_part_event_count DESC,
        associated_failure_cost_gbp DESC,
        associated_attributed_downtime_minutes DESC,
        part_code
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_observed_part_event_count,
    SUM(associated_attributed_downtime_minutes) OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        recorded_part_quantity DESC,
        observed_part_event_count DESC,
        associated_failure_cost_gbp DESC,
        associated_attributed_downtime_minutes DESC,
        part_code
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_associated_attributed_downtime_minutes,
    ROUND(
      SUM(associated_failure_cost_gbp) OVER (
        PARTITION BY event_month, client_id, site_id, model
        ORDER BY
          recorded_part_quantity DESC,
          observed_part_event_count DESC,
          associated_failure_cost_gbp DESC,
          associated_attributed_downtime_minutes DESC,
          part_code
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ),
      2
    ) AS cumulative_associated_failure_cost_gbp
  FROM part_usage
)
SELECT
  event_month,
  client_id,
  site_id,
  model,
  part_code,
  part_usage_rank,
  recorded_part_count,
  observed_part_event_count,
  observed_part_identity_count,
  affected_machine_count,
  observed_part_day_count,
  associated_fault_count,
  recorded_part_quantity,
  associated_attributed_downtime_minutes,
  associated_failure_cost_gbp,
  total_recorded_part_quantity,
  total_observed_part_event_count,
  total_associated_attributed_downtime_minutes,
  total_associated_failure_cost_gbp,
  CASE
    WHEN total_recorded_part_quantity > 0 THEN
      ROUND(recorded_part_quantity / total_recorded_part_quantity * 100, 2)
    ELSE NULL
  END AS recorded_part_quantity_share_pct,
  CASE
    WHEN total_recorded_part_quantity > 0 THEN
      ROUND(
        cumulative_recorded_part_quantity
          / total_recorded_part_quantity * 100,
        2
      )
    ELSE NULL
  END AS cumulative_recorded_part_quantity_share_pct,
  CASE
    WHEN total_observed_part_event_count > 0 THEN
      ROUND(
        observed_part_event_count / total_observed_part_event_count * 100,
        2
      )
    ELSE NULL
  END AS observed_part_event_share_pct,
  CASE
    WHEN total_observed_part_event_count > 0 THEN
      ROUND(
        cumulative_observed_part_event_count
          / total_observed_part_event_count * 100,
        2
      )
    ELSE NULL
  END AS cumulative_observed_part_event_share_pct,
  CASE
    WHEN total_associated_attributed_downtime_minutes > 0 THEN
      ROUND(
        associated_attributed_downtime_minutes
          / total_associated_attributed_downtime_minutes * 100,
        2
      )
    ELSE NULL
  END AS associated_attributed_downtime_share_pct,
  CASE
    WHEN total_associated_attributed_downtime_minutes > 0 THEN
      ROUND(
        cumulative_associated_attributed_downtime_minutes
          / total_associated_attributed_downtime_minutes * 100,
        2
      )
    ELSE NULL
  END AS cumulative_associated_attributed_downtime_share_pct,
  CASE
    WHEN total_associated_failure_cost_gbp > 0 THEN
      ROUND(
        associated_failure_cost_gbp
          / total_associated_failure_cost_gbp * 100,
        2
      )
    ELSE NULL
  END AS associated_failure_cost_share_pct,
  CASE
    WHEN total_associated_failure_cost_gbp > 0 THEN
      ROUND(
        cumulative_associated_failure_cost_gbp
          / total_associated_failure_cost_gbp * 100,
        2
      )
    ELSE NULL
  END AS cumulative_associated_failure_cost_share_pct,
  CASE
    WHEN total_associated_attributed_downtime_minutes <= 0
      AND total_associated_failure_cost_gbp <= 0
      THEN 'part_quantity_without_recorded_impact'
    WHEN total_associated_failure_cost_gbp <= 0
      THEN 'part_quantity_without_recorded_cost'
    ELSE 'part_usage_evidence_observed'
  END AS part_usage_evidence_status,
  'observed_failure_part_records_only' AS part_usage_scope
FROM ranked_parts
ORDER BY
  event_month DESC,
  client_id,
  site_id,
  model,
  part_usage_rank,
  part_code;
