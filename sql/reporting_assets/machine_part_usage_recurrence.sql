WITH part_events AS (
  SELECT
    date_dim.date_day AS event_date,
    failure_fact.event_ts_utc,
    failure_fact.event_id,
    client_dim.client_id,
    site_dim.site_id,
    machine_dim.machine_id,
    model_dim.model,
    fault_dim.fault_code,
    fault_dim.severity,
    fault_dim.severity_rank,
    failure_fact.part_code,
    COALESCE(failure_fact.part_quantity, 0) AS part_quantity,
    COALESCE(failure_fact.downtime_minutes, 0)
      AS associated_attributed_downtime_minutes,
    COALESCE(failure_fact.maintenance_cost_gbp, 0)
      AS associated_failure_cost_gbp,
    failure_fact.failure_event_count
  FROM main.lakehouse_demo.fact_machine_failure_event AS failure_fact
  INNER JOIN main.lakehouse_demo.dim_date AS date_dim
    ON failure_fact.date_key = date_dim.date_key
  INNER JOIN main.lakehouse_demo.dim_client AS client_dim
    ON failure_fact.client_key = client_dim.client_key
  INNER JOIN main.lakehouse_demo.dim_site AS site_dim
    ON failure_fact.site_key = site_dim.site_key
  INNER JOIN main.lakehouse_demo.dim_machine AS machine_dim
    ON failure_fact.machine_key = machine_dim.machine_key
  INNER JOIN main.lakehouse_demo.dim_model AS model_dim
    ON failure_fact.model_key = model_dim.model_key
  INNER JOIN main.lakehouse_demo.dim_fault AS fault_dim
    ON failure_fact.fault_key = fault_dim.fault_key
  WHERE failure_fact.part_code IS NOT NULL
    AND UPPER(TRIM(failure_fact.part_code)) <> 'NONE'
    AND COALESCE(failure_fact.part_quantity, 0) > 0
),
usage_summary AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    part_code,
    MIN(event_date) AS first_observed_part_use_date,
    MAX(event_date) AS latest_observed_part_use_date,
    MIN(event_ts_utc) AS first_observed_part_use_at_utc,
    MAX(event_ts_utc) AS latest_observed_part_use_at_utc,
    SUM(failure_event_count) AS observed_part_event_count,
    COUNT(DISTINCT event_id) AS observed_part_identity_count,
    COUNT(DISTINCT event_date) AS observed_part_day_count,
    COUNT(
      DISTINCT DATE_TRUNC('month', CAST(event_date AS TIMESTAMP))
    ) AS observed_part_month_count,
    SUM(part_quantity) AS total_recorded_part_quantity,
    SUM(associated_attributed_downtime_minutes)
      AS associated_attributed_downtime_minutes,
    ROUND(SUM(associated_failure_cost_gbp), 2)
      AS associated_failure_cost_gbp
  FROM part_events
  GROUP BY
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    part_code
),
usage_dates AS (
  SELECT DISTINCT
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    part_code,
    event_date
  FROM part_events
),
sequenced_dates AS (
  SELECT
    usage_dates.*,
    LAG(event_date) OVER (
      PARTITION BY
        client_id,
        site_id,
        machine_id,
        model,
        fault_code,
        severity,
        severity_rank,
        part_code
      ORDER BY event_date
    ) AS previous_observed_part_use_date
  FROM usage_dates
),
interval_summary AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    part_code,
    COUNT(
      CASE
        WHEN previous_observed_part_use_date IS NOT NULL THEN 1
        ELSE NULL
      END
    ) AS recurrence_interval_observation_count,
    MIN(
      CASE
        WHEN previous_observed_part_use_date IS NOT NULL THEN
          DATEDIFF(event_date, previous_observed_part_use_date)
        ELSE NULL
      END
    ) AS min_days_between_observed_part_use_dates,
    ROUND(
      AVG(
        CASE
          WHEN previous_observed_part_use_date IS NOT NULL THEN
            DATEDIFF(event_date, previous_observed_part_use_date)
          ELSE NULL
        END
      ),
      2
    ) AS avg_days_between_observed_part_use_dates,
    MAX(
      CASE
        WHEN previous_observed_part_use_date IS NOT NULL THEN
          DATEDIFF(event_date, previous_observed_part_use_date)
        ELSE NULL
      END
    ) AS max_days_between_observed_part_use_dates
  FROM sequenced_dates
  GROUP BY
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    part_code
),
combined AS (
  SELECT
    usage.*,
    intervals.recurrence_interval_observation_count,
    intervals.min_days_between_observed_part_use_dates,
    intervals.avg_days_between_observed_part_use_dates,
    intervals.max_days_between_observed_part_use_dates
  FROM usage_summary AS usage
  LEFT JOIN interval_summary AS intervals
    ON usage.client_id = intervals.client_id
   AND usage.site_id = intervals.site_id
   AND usage.machine_id = intervals.machine_id
   AND usage.model <=> intervals.model
   AND usage.fault_code = intervals.fault_code
   AND usage.severity = intervals.severity
   AND usage.part_code = intervals.part_code
),
ranked AS (
  SELECT
    combined.*,
    ROW_NUMBER() OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY
        total_recorded_part_quantity DESC,
        observed_part_event_count DESC,
        associated_failure_cost_gbp DESC,
        severity_rank DESC,
        machine_id,
        fault_code,
        part_code,
        severity
    ) AS observed_part_usage_rank
  FROM combined
)
SELECT
  client_id,
  site_id,
  machine_id,
  model,
  fault_code,
  severity,
  severity_rank,
  part_code,
  observed_part_usage_rank,
  first_observed_part_use_date,
  latest_observed_part_use_date,
  first_observed_part_use_at_utc,
  latest_observed_part_use_at_utc,
  observed_part_event_count,
  observed_part_identity_count,
  CASE
    WHEN observed_part_event_count > 0 THEN observed_part_event_count - 1
    ELSE 0
  END AS repeat_observed_part_event_count,
  observed_part_day_count,
  observed_part_month_count,
  DATEDIFF(
    latest_observed_part_use_date,
    first_observed_part_use_date
  ) + 1 AS observed_part_use_calendar_span_days,
  recurrence_interval_observation_count,
  min_days_between_observed_part_use_dates,
  avg_days_between_observed_part_use_dates,
  max_days_between_observed_part_use_dates,
  total_recorded_part_quantity,
  CASE
    WHEN observed_part_event_count > 0 THEN
      ROUND(total_recorded_part_quantity / observed_part_event_count, 2)
    ELSE NULL
  END AS avg_recorded_part_quantity_per_event,
  associated_attributed_downtime_minutes,
  CASE
    WHEN observed_part_event_count > 0 THEN
      ROUND(
        associated_attributed_downtime_minutes / observed_part_event_count,
        2
      )
    ELSE NULL
  END AS avg_associated_downtime_per_part_event_minutes,
  associated_failure_cost_gbp,
  CASE
    WHEN observed_part_event_count > 0 THEN
      ROUND(associated_failure_cost_gbp / observed_part_event_count, 2)
    ELSE NULL
  END AS avg_associated_failure_cost_per_part_event_gbp,
  CASE
    WHEN observed_part_event_count = 1
      THEN 'single_observed_part_event'
    WHEN observed_part_day_count = 1
      THEN 'repeat_part_events_on_one_observed_day'
    WHEN observed_part_month_count = 1
      THEN 'repeat_part_events_across_observed_days'
    ELSE 'repeat_part_events_across_observed_months'
  END AS part_usage_recurrence_status,
  'observed_failure_part_records_only' AS part_usage_scope
FROM ranked
ORDER BY
  client_id,
  site_id,
  model,
  observed_part_usage_rank,
  machine_id,
  fault_code,
  part_code,
  severity;
