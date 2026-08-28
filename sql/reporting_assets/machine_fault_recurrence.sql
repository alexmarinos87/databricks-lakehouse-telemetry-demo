WITH failure_events AS (
  SELECT
    client_dim.client_id,
    site_dim.site_id,
    machine_dim.machine_id,
    model_dim.model,
    fault_dim.fault_code,
    fault_dim.severity,
    fault_dim.severity_rank,
    failure_fact.event_id,
    date_dim.date_day AS event_date,
    failure_fact.event_ts_utc,
    COALESCE(failure_fact.failure_event_count, 0)
      AS failure_event_count,
    COALESCE(failure_fact.downtime_minutes, 0)
      AS attributed_downtime_minutes,
    COALESCE(failure_fact.maintenance_cost_gbp, 0)
      AS maintenance_cost_gbp
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
),
event_summary AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    MIN(event_date) AS first_observed_failure_date,
    MAX(event_date) AS latest_observed_failure_date,
    MIN(event_ts_utc) AS first_observed_failure_at_utc,
    MAX(event_ts_utc) AS latest_observed_failure_at_utc,
    SUM(failure_event_count) AS observed_failure_event_count,
    COUNT(DISTINCT event_id) AS observed_failure_identity_count,
    COUNT(DISTINCT event_date) AS observed_failure_day_count,
    COUNT(
      DISTINCT DATE_TRUNC('month', CAST(event_date AS TIMESTAMP))
    ) AS observed_failure_month_count,
    SUM(attributed_downtime_minutes)
      AS attributed_downtime_minutes,
    ROUND(SUM(maintenance_cost_gbp), 2)
      AS failure_related_cost_gbp
  FROM failure_events
  GROUP BY
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank
),
failure_dates AS (
  SELECT DISTINCT
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    event_date
  FROM failure_events
),
sequenced_failure_dates AS (
  SELECT
    failure_dates.*,
    LAG(event_date) OVER (
      PARTITION BY
        client_id,
        site_id,
        machine_id,
        model,
        fault_code,
        severity,
        severity_rank
      ORDER BY event_date
    ) AS previous_observed_failure_date
  FROM failure_dates
),
date_gap_summary AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank,
    SUM(
      CASE
        WHEN previous_observed_failure_date IS NOT NULL THEN 1
        ELSE 0
      END
    ) AS recurrence_interval_observation_count,
    MIN(
      CASE
        WHEN previous_observed_failure_date IS NOT NULL THEN
          DATEDIFF(event_date, previous_observed_failure_date)
        ELSE NULL
      END
    ) AS min_days_between_observed_failure_days,
    ROUND(
      AVG(
        CASE
          WHEN previous_observed_failure_date IS NOT NULL THEN
            DATEDIFF(event_date, previous_observed_failure_date)
          ELSE NULL
        END
      ),
      2
    ) AS avg_days_between_observed_failure_days,
    MAX(
      CASE
        WHEN previous_observed_failure_date IS NOT NULL THEN
          DATEDIFF(event_date, previous_observed_failure_date)
        ELSE NULL
      END
    ) AS max_days_between_observed_failure_days
  FROM sequenced_failure_dates
  GROUP BY
    client_id,
    site_id,
    machine_id,
    model,
    fault_code,
    severity,
    severity_rank
),
recurrence_base AS (
  SELECT
    events.*,
    DATEDIFF(
      events.latest_observed_failure_date,
      events.first_observed_failure_date
    ) + 1 AS observed_failure_calendar_span_days,
    CASE
      WHEN events.observed_failure_event_count > 0 THEN
        events.observed_failure_event_count - 1
      ELSE 0
    END AS repeat_observed_failure_event_count,
    COALESCE(gaps.recurrence_interval_observation_count, 0)
      AS recurrence_interval_observation_count,
    gaps.min_days_between_observed_failure_days,
    gaps.avg_days_between_observed_failure_days,
    gaps.max_days_between_observed_failure_days,
    CASE
      WHEN events.observed_failure_event_count > 0 THEN ROUND(
        events.attributed_downtime_minutes
          / events.observed_failure_event_count,
        2
      )
      ELSE NULL
    END AS avg_attributed_downtime_per_failure_event_minutes,
    CASE
      WHEN events.observed_failure_event_count > 0 THEN ROUND(
        events.failure_related_cost_gbp
          / events.observed_failure_event_count,
        2
      )
      ELSE NULL
    END AS avg_failure_related_cost_per_event_gbp
  FROM event_summary AS events
  LEFT JOIN date_gap_summary AS gaps
    ON events.client_id = gaps.client_id
   AND events.site_id = gaps.site_id
   AND events.machine_id = gaps.machine_id
   AND events.model <=> gaps.model
   AND events.fault_code = gaps.fault_code
   AND events.severity = gaps.severity
   AND events.severity_rank = gaps.severity_rank
),
ranked_recurrence AS (
  SELECT
    recurrence_base.*,
    ROW_NUMBER() OVER (
      PARTITION BY client_id, site_id, model
      ORDER BY
        observed_failure_event_count DESC,
        observed_failure_day_count DESC,
        attributed_downtime_minutes DESC,
        failure_related_cost_gbp DESC,
        severity_rank DESC,
        machine_id,
        fault_code,
        severity
    ) AS observed_recurrence_rank
  FROM recurrence_base
)
SELECT
  client_id,
  site_id,
  machine_id,
  model,
  fault_code,
  severity,
  severity_rank,
  observed_recurrence_rank,
  first_observed_failure_date,
  latest_observed_failure_date,
  first_observed_failure_at_utc,
  latest_observed_failure_at_utc,
  observed_failure_event_count,
  observed_failure_identity_count,
  repeat_observed_failure_event_count,
  observed_failure_day_count,
  observed_failure_month_count,
  observed_failure_calendar_span_days,
  recurrence_interval_observation_count,
  min_days_between_observed_failure_days,
  avg_days_between_observed_failure_days,
  max_days_between_observed_failure_days,
  attributed_downtime_minutes,
  failure_related_cost_gbp,
  avg_attributed_downtime_per_failure_event_minutes,
  avg_failure_related_cost_per_event_gbp,
  CASE
    WHEN observed_failure_event_count <= 1
      THEN 'single_observed_failure_event'
    WHEN observed_failure_day_count = 1
      THEN 'repeat_events_on_one_observed_day'
    WHEN observed_failure_month_count = 1
      THEN 'repeat_events_across_observed_days'
    ELSE 'repeat_events_across_observed_months'
  END AS observed_recurrence_status,
  'observed_failure_events_only' AS recurrence_scope
FROM ranked_recurrence
ORDER BY
  client_id,
  site_id,
  model,
  observed_recurrence_rank,
  machine_id,
  fault_code,
  severity;
