WITH latest_quality AS (
  SELECT
    quality_run_id,
    checked_at,
    failed_error_check_count,
    failed_warning_check_count,
    ROW_NUMBER() OVER (
      ORDER BY checked_at DESC, quality_run_id DESC
    ) AS recency_rank
  FROM main.lakehouse_demo.quality_metric_history
),
latest_publication AS (
  SELECT
    forecast_run_id,
    publication_state,
    publication_started_at_utc,
    publication_completed_at_utc,
    ROW_NUMBER() OVER (
      ORDER BY publication_started_at_utc DESC, forecast_run_id DESC
    ) AS recency_rank
  FROM main.lakehouse_demo.gold_downtime_forecast_publication_manifest
),
ingestion_identity AS (
  SELECT
    COUNT_IF(NOT COALESCE(_source_identity_valid, FALSE)) AS invalid_identity_row_count,
    MAX(_ingested_at) AS latest_ingested_at
  FROM main.lakehouse_demo.bronze_machine_events
),
health_candidates AS (
  SELECT
    'quality_error_check_failed' AS alert_id,
    'critical' AS severity,
    CASE
      WHEN failed_error_check_count > 0 THEN 'firing'
      ELSE 'ok'
    END AS alert_state,
    CAST(failed_error_check_count AS STRING) AS observed_value,
    checked_at AS observed_at,
    quality_run_id AS evidence_id
  FROM latest_quality
  WHERE recency_rank = 1

  UNION ALL

  SELECT
    'quality_warning_check_failed' AS alert_id,
    'warning' AS severity,
    CASE
      WHEN failed_warning_check_count > 0 THEN 'firing'
      ELSE 'ok'
    END AS alert_state,
    CAST(failed_warning_check_count AS STRING) AS observed_value,
    checked_at AS observed_at,
    quality_run_id AS evidence_id
  FROM latest_quality
  WHERE recency_rank = 1

  UNION ALL

  SELECT
    'forecast_publication_failed' AS alert_id,
    'critical' AS severity,
    CASE
      WHEN publication_state = 'FAILED' THEN 'firing'
      ELSE 'ok'
    END AS alert_state,
    publication_state AS observed_value,
    COALESCE(publication_completed_at_utc, publication_started_at_utc) AS observed_at,
    forecast_run_id AS evidence_id
  FROM latest_publication
  WHERE recency_rank = 1

  UNION ALL

  SELECT
    'forecast_publication_stuck' AS alert_id,
    'warning' AS severity,
    CASE
      WHEN publication_state = 'STARTED'
       AND publication_started_at_utc < current_timestamp() - INTERVAL 60 MINUTES
      THEN 'firing'
      ELSE 'ok'
    END AS alert_state,
    publication_state AS observed_value,
    publication_started_at_utc AS observed_at,
    forecast_run_id AS evidence_id
  FROM latest_publication
  WHERE recency_rank = 1

  UNION ALL

  SELECT
    'invalid_ingestion_identity' AS alert_id,
    'critical' AS severity,
    CASE
      WHEN invalid_identity_row_count > 0 THEN 'firing'
      ELSE 'ok'
    END AS alert_state,
    CAST(invalid_identity_row_count AS STRING) AS observed_value,
    latest_ingested_at AS observed_at,
    'bronze_machine_events' AS evidence_id
  FROM ingestion_identity
)
SELECT
  alert_id,
  severity,
  alert_state,
  observed_value,
  observed_at,
  evidence_id
FROM health_candidates
ORDER BY
  CASE severity WHEN 'critical' THEN 1 ELSE 2 END,
  alert_id
LIMIT 100;
