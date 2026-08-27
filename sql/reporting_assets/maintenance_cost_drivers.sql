WITH maintenance AS (
  SELECT
    event_month,
    client_id,
    site_id,
    model,
    SUM(maintenance_event_count) AS maintenance_event_count,
    SUM(failure_event_count) AS failure_event_count,
    ROUND(SUM(COALESCE(maintenance_cost_gbp, 0)), 2)
      AS maintenance_cost_gbp,
    SUM(COALESCE(downtime_minutes, 0))
      AS attributed_downtime_minutes
  FROM main.lakehouse_demo.gold_maintenance_costs
  GROUP BY event_month, client_id, site_id, model
),
part_totals AS (
  SELECT
    DATE_TRUNC('MONTH', CAST(event_date AS TIMESTAMP)) AS event_month,
    client_id,
    site_id,
    model,
    part_code,
    SUM(part_quantity) AS part_quantity,
    ROUND(SUM(COALESCE(associated_cost_gbp, 0)), 2)
      AS associated_cost_gbp
  FROM main.lakehouse_demo.gold_parts_usage
  GROUP BY
    DATE_TRUNC('MONTH', CAST(event_date AS TIMESTAMP)),
    client_id,
    site_id,
    model,
    part_code
),
ranked_parts AS (
  SELECT
    event_month,
    client_id,
    site_id,
    model,
    part_code,
    part_quantity,
    associated_cost_gbp,
    ROW_NUMBER() OVER (
      PARTITION BY event_month, client_id, site_id, model
      ORDER BY
        part_quantity DESC,
        associated_cost_gbp DESC,
        part_code
    ) AS part_rank
  FROM part_totals
)
SELECT
  m.event_month,
  m.client_id,
  m.site_id,
  m.model,
  m.maintenance_event_count,
  m.failure_event_count,
  m.maintenance_cost_gbp,
  m.attributed_downtime_minutes,
  CASE
    WHEN m.maintenance_event_count > 0 THEN
      ROUND(m.maintenance_cost_gbp / m.maintenance_event_count, 2)
    ELSE NULL
  END AS cost_per_maintenance_event_gbp,
  CASE
    WHEN m.failure_event_count > 0 THEN
      ROUND(m.maintenance_cost_gbp / m.failure_event_count, 2)
    ELSE NULL
  END AS cost_per_failure_event_gbp,
  CASE
    WHEN m.failure_event_count > 0 THEN
      ROUND(m.attributed_downtime_minutes / m.failure_event_count, 2)
    ELSE NULL
  END AS attributed_downtime_per_failure_minutes,
  p.part_code AS top_recorded_part_code,
  p.part_quantity AS top_recorded_part_quantity,
  p.associated_cost_gbp AS top_recorded_part_cost_gbp,
  CASE
    WHEN m.maintenance_cost_gbp > 0 AND p.associated_cost_gbp IS NOT NULL THEN
      ROUND(p.associated_cost_gbp / m.maintenance_cost_gbp * 100, 2)
    ELSE NULL
  END AS top_recorded_part_cost_share_pct,
  CASE
    WHEN m.maintenance_event_count = 0
      AND m.failure_event_count = 0
      AND m.maintenance_cost_gbp = 0
      AND m.attributed_downtime_minutes = 0
      THEN 'no_recorded_maintenance_impact'
    WHEN m.failure_event_count = 0
      THEN 'maintenance_without_recorded_failure'
    WHEN p.part_code IS NULL
      THEN 'failure_without_recorded_part'
    ELSE 'failure_with_recorded_part'
  END AS maintenance_observation_status
FROM maintenance AS m
LEFT JOIN ranked_parts AS p
  ON m.event_month = p.event_month
 AND m.client_id = p.client_id
 AND m.site_id = p.site_id
 AND m.model <=> p.model
 AND p.part_rank = 1
ORDER BY
  m.maintenance_cost_gbp DESC,
  m.attributed_downtime_minutes DESC,
  m.event_month DESC,
  m.client_id,
  m.site_id,
  m.model;
