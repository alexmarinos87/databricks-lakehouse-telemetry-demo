-- BI-ready reporting queries for the Databricks Lakehouse demo.
-- Replace `main.lakehouse_demo` if you run the notebooks with different widget values.

-- Daily uptime by site and model
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

-- Highest cost failure events
SELECT
  event_date,
  site_id,
  client_id,
  machine_id,
  model,
  fault_code,
  severity,
  downtime_minutes,
  maintenance_cost_gbp
FROM main.lakehouse_demo.gold_failure_events
ORDER BY maintenance_cost_gbp DESC, downtime_minutes DESC;

-- Maintenance cost by month and model
SELECT
  event_month,
  site_id,
  model,
  SUM(maintenance_event_count) AS maintenance_events,
  SUM(failure_event_count) AS failure_events,
  ROUND(SUM(maintenance_cost_gbp), 2) AS maintenance_cost_gbp,
  SUM(downtime_minutes) AS downtime_minutes
FROM main.lakehouse_demo.gold_maintenance_costs
GROUP BY event_month, site_id, model
ORDER BY event_month, maintenance_cost_gbp DESC;

-- Parts usage by site
SELECT
  event_date,
  site_id,
  part_code,
  SUM(part_quantity) AS part_quantity,
  ROUND(SUM(associated_cost_gbp), 2) AS associated_cost_gbp,
  SUM(machine_count) AS impacted_machine_count
FROM main.lakehouse_demo.gold_parts_usage
GROUP BY event_date, site_id, part_code
ORDER BY event_date, associated_cost_gbp DESC;

-- Client asset summary for account and service reviews
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

-- Latest failed quality checks for pipeline monitoring
WITH latest_quality_run AS (
  SELECT MAX(checked_at) AS checked_at
  FROM main.lakehouse_demo.quality_check_results
)
SELECT
  q.checked_at,
  q.severity,
  q.check_name,
  q.status,
  q.detail
FROM main.lakehouse_demo.quality_check_results AS q
INNER JOIN latest_quality_run AS latest
  ON q.checked_at = latest.checked_at
WHERE q.status = 'fail'
ORDER BY
  CASE q.severity
    WHEN 'error' THEN 1
    WHEN 'warning' THEN 2
    ELSE 3
  END,
  q.check_name;

-- Recent quality check run history for monitoring trends
SELECT
  checked_at,
  check_count,
  passed_check_count,
  failed_check_count,
  failed_error_check_count,
  failed_warning_check_count,
  all_error_checks_passed
FROM main.lakehouse_demo.quality_metric_history
ORDER BY checked_at DESC
LIMIT 30;
