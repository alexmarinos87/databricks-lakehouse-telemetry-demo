SELECT
  date_dim.date_day AS event_date,
  client_dim.client_id,
  site_dim.site_id,
  model_dim.model,
  fault_dim.fault_code,
  fault_dim.severity,
  fault_dim.severity_rank,
  SUM(failure_fact.failure_event_count) AS failure_event_count,
  COUNT(DISTINCT failure_fact.machine_key) AS affected_machine_count,
  SUM(failure_fact.downtime_minutes) AS downtime_minutes,
  ROUND(SUM(failure_fact.maintenance_cost_gbp), 2) AS maintenance_cost_gbp
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
  date_dim.date_day,
  client_dim.client_id,
  site_dim.site_id,
  model_dim.model,
  fault_dim.fault_code,
  fault_dim.severity,
  fault_dim.severity_rank
ORDER BY
  event_date DESC,
  fault_dim.severity_rank DESC,
  maintenance_cost_gbp DESC;
