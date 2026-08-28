WITH daily_observations AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    event_date,
    SUM(COALESCE(running_minutes, 0)) AS running_minutes,
    SUM(COALESCE(observed_minutes, 0)) AS observed_minutes
  FROM main.lakehouse_demo.gold_machine_uptime
  WHERE event_date IS NOT NULL
  GROUP BY
    client_id,
    site_id,
    machine_id,
    model,
    event_date
),
sequenced_observations AS (
  SELECT
    daily_observations.*,
    LAG(event_date) OVER (
      PARTITION BY client_id, site_id, machine_id, model
      ORDER BY event_date
    ) AS previous_observed_date
  FROM daily_observations
),
observation_gaps AS (
  SELECT
    sequenced_observations.*,
    CASE
      WHEN previous_observed_date IS NULL THEN NULL
      ELSE DATEDIFF(event_date, previous_observed_date)
    END AS observed_date_gap_days,
    CASE
      WHEN previous_observed_date IS NOT NULL
       AND DATEDIFF(event_date, previous_observed_date) > 1
        THEN DATEDIFF(event_date, previous_observed_date) - 1
      ELSE 0
    END AS unobserved_days_between_observations
  FROM sequenced_observations
),
continuity_summary AS (
  SELECT
    client_id,
    site_id,
    machine_id,
    model,
    MIN(event_date) AS first_observed_date,
    MAX(event_date) AS last_observed_date,
    COUNT(*) AS observed_day_count,
    DATEDIFF(MAX(event_date), MIN(event_date)) + 1 AS calendar_span_days,
    SUM(
      CASE
        WHEN previous_observed_date IS NOT NULL
         AND observed_date_gap_days = 1 THEN 1
        ELSE 0
      END
    ) AS consecutive_observation_pair_count,
    SUM(
      CASE
        WHEN unobserved_days_between_observations > 0 THEN 1
        ELSE 0
      END
    ) AS observation_gap_count,
    SUM(unobserved_days_between_observations)
      AS unobserved_days_within_span,
    MAX(unobserved_days_between_observations)
      AS max_unobserved_gap_days,
    ROUND(
      AVG(
        CASE
          WHEN unobserved_days_between_observations > 0
            THEN unobserved_days_between_observations
          ELSE NULL
        END
      ),
      2
    ) AS avg_unobserved_gap_days,
    SUM(
      CASE
        WHEN observed_minutes <= 0 THEN 1
        ELSE 0
      END
    ) AS no_observed_duration_day_count,
    SUM(
      CASE
        WHEN running_minutes <= 0 THEN 1
        ELSE 0
      END
    ) AS no_operating_time_day_count,
    SUM(running_minutes) AS running_minutes,
    SUM(observed_minutes) AS observed_minutes
  FROM observation_gaps
  GROUP BY
    client_id,
    site_id,
    machine_id,
    model
)
SELECT
  client_id,
  site_id,
  machine_id,
  model,
  first_observed_date,
  last_observed_date,
  observed_day_count,
  calendar_span_days,
  CASE
    WHEN calendar_span_days > 0 THEN
      ROUND(observed_day_count / calendar_span_days * 100, 2)
    ELSE NULL
  END AS observed_day_coverage_pct,
  consecutive_observation_pair_count,
  observation_gap_count,
  unobserved_days_within_span,
  max_unobserved_gap_days,
  avg_unobserved_gap_days,
  no_observed_duration_day_count,
  no_operating_time_day_count,
  ROUND(running_minutes / 60.0, 2) AS operating_hours,
  ROUND(observed_minutes / 60.0, 2) AS observed_hours,
  CASE
    WHEN observed_minutes <= 0 THEN 'no_observed_duration'
    WHEN observed_day_count = 1 THEN 'single_observed_day'
    WHEN observation_gap_count = 0 THEN 'continuous_observed_dates'
    ELSE 'intermittent_observed_dates'
  END AS observation_continuity_status,
  'observed_date_span_only' AS continuity_scope
FROM continuity_summary
ORDER BY
  observed_day_coverage_pct ASC NULLS LAST,
  max_unobserved_gap_days DESC,
  no_observed_duration_day_count DESC,
  client_id,
  site_id,
  machine_id,
  model;
