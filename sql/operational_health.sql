-- Operational health queries for the synthetic lakehouse demo.
-- Replace main.lakehouse_demo through the existing target-aware publication process
-- before using these statements outside the default local namespace.

-- 1. Latest persisted data-quality run.
WITH ranked_quality_runs AS (
  SELECT
    quality_run_id,
    checked_at,
    check_count,
    passed_check_count,
    failed_check_count,
    failed_error_check_count,
    failed_warning_check_count,
    all_error_checks_passed,
    ROW_NUMBER() OVER (
      ORDER BY checked_at DESC, quality_run_id DESC
    ) AS run_rank
  FROM main.lakehouse_demo.quality_metric_history
)
SELECT
  quality_run_id,
  checked_at,
  check_count,
  passed_check_count,
  failed_check_count,
  failed_error_check_count,
  failed_warning_check_count,
  all_error_checks_passed,
  TIMESTAMPDIFF(HOUR, checked_at, CURRENT_TIMESTAMP()) AS evidence_age_hours
FROM ranked_quality_runs
WHERE run_rank = 1
LIMIT 1;

-- 2. Recent forecast publication attempts and current-commit status.
WITH ranked_commits AS (
  SELECT
    forecast_run_id,
    ROW_NUMBER() OVER (
      ORDER BY publication_completed_at_utc DESC, forecast_run_id DESC
    ) AS commit_rank
  FROM main.lakehouse_demo.gold_downtime_forecast_publication_manifest
  WHERE publication_state = 'COMMITTED'
)
SELECT
  manifest.forecast_run_id,
  manifest.publication_state,
  manifest.publication_started_at_utc,
  manifest.publication_completed_at_utc,
  manifest.forecast_row_count,
  manifest.validation_row_count,
  manifest.forecast_payload_sha256,
  manifest.validation_payload_sha256,
  COALESCE(commits.commit_rank = 1, FALSE) AS is_current_committed_run,
  CASE
    WHEN manifest.publication_state = 'STARTED'
      THEN TIMESTAMPDIFF(
        MINUTE,
        manifest.publication_started_at_utc,
        CURRENT_TIMESTAMP()
      )
    ELSE NULL
  END AS started_age_minutes
FROM main.lakehouse_demo.gold_downtime_forecast_publication_manifest AS manifest
LEFT JOIN ranked_commits AS commits
  ON manifest.forecast_run_id = commits.forecast_run_id
ORDER BY manifest.publication_started_at_utc DESC, manifest.forecast_run_id DESC
LIMIT 100;

-- 3. Immutable ingestion identity coverage by arrival date and mode.
SELECT
  CAST(_ingested_at AS DATE) AS ingestion_date,
  COALESCE(NULLIF(_ingestion_mode, ''), 'invalid') AS ingestion_mode,
  COUNT(*) AS row_count,
  COUNT(DISTINCT _source_object_name) AS source_object_count,
  SUM(
    CASE
      WHEN COALESCE(_source_identity_valid, FALSE) THEN 0
      ELSE 1
    END
  ) AS invalid_source_identity_row_count,
  COUNT(DISTINCT _replay_id) AS replay_id_count
FROM main.lakehouse_demo.bronze_machine_events
GROUP BY
  CAST(_ingested_at AS DATE),
  COALESCE(NULLIF(_ingestion_mode, ''), 'invalid')
ORDER BY ingestion_date DESC, ingestion_mode
LIMIT 100;

-- 4. Bounded alert candidates. This query identifies policy conditions only;
-- it does not send a notification or claim a live alert integration.
WITH latest_quality AS (
  SELECT *
  FROM (
    SELECT
      quality_run_id,
      checked_at,
      failed_error_check_count,
      failed_warning_check_count,
      ROW_NUMBER() OVER (
        ORDER BY checked_at DESC, quality_run_id DESC
      ) AS run_rank
    FROM main.lakehouse_demo.quality_metric_history
  )
  WHERE run_rank = 1
),
latest_forecast AS (
  SELECT *
  FROM (
    SELECT
      forecast_run_id,
      publication_state,
      publication_started_at_utc,
      publication_completed_at_utc,
      ROW_NUMBER() OVER (
        ORDER BY publication_started_at_utc DESC, forecast_run_id DESC
      ) AS run_rank
    FROM main.lakehouse_demo.gold_downtime_forecast_publication_manifest
  )
  WHERE run_rank = 1
),
ingestion_identity AS (
  SELECT
    SUM(
      CASE
        WHEN COALESCE(_source_identity_valid, FALSE) THEN 0
        ELSE 1
      END
    ) AS invalid_source_identity_row_count
  FROM main.lakehouse_demo.bronze_machine_events
)
SELECT
  'quality_error_check_failed' AS alert_id,
  'critical' AS severity,
  'lakehouse-demo-engineers' AS owner_role,
  CAST(quality_run_id AS STRING) AS evidence_id,
  CAST(checked_at AS TIMESTAMP) AS observed_at,
  CAST(failed_error_check_count AS BIGINT) AS observed_count
FROM latest_quality
WHERE failed_error_check_count > 0

UNION ALL

SELECT
  'quality_evidence_stale' AS alert_id,
  'warning' AS severity,
  'lakehouse-demo-engineers' AS owner_role,
  CAST(quality_run_id AS STRING) AS evidence_id,
  CAST(checked_at AS TIMESTAMP) AS observed_at,
  CAST(TIMESTAMPDIFF(HOUR, checked_at, CURRENT_TIMESTAMP()) AS BIGINT)
    AS observed_count
FROM latest_quality
WHERE TIMESTAMPDIFF(HOUR, checked_at, CURRENT_TIMESTAMP()) >= 24

UNION ALL

SELECT
  'forecast_publication_failed' AS alert_id,
  'critical' AS severity,
  'lakehouse-demo-engineers' AS owner_role,
  CAST(forecast_run_id AS STRING) AS evidence_id,
  CAST(publication_started_at_utc AS TIMESTAMP) AS observed_at,
  CAST(1 AS BIGINT) AS observed_count
FROM latest_forecast
WHERE publication_state = 'FAILED'

UNION ALL

SELECT
  'forecast_publication_stuck' AS alert_id,
  'warning' AS severity,
  'lakehouse-demo-engineers' AS owner_role,
  CAST(forecast_run_id AS STRING) AS evidence_id,
  CAST(publication_started_at_utc AS TIMESTAMP) AS observed_at,
  CAST(
    TIMESTAMPDIFF(
      MINUTE,
      publication_started_at_utc,
      CURRENT_TIMESTAMP()
    ) AS BIGINT
  ) AS observed_count
FROM latest_forecast
WHERE publication_state = 'STARTED'
  AND TIMESTAMPDIFF(
    MINUTE,
    publication_started_at_utc,
    CURRENT_TIMESTAMP()
  ) > 60

UNION ALL

SELECT
  'invalid_ingestion_identity' AS alert_id,
  'critical' AS severity,
  'lakehouse-demo-engineers' AS owner_role,
  'bronze_machine_events' AS evidence_id,
  CURRENT_TIMESTAMP() AS observed_at,
  CAST(invalid_source_identity_row_count AS BIGINT) AS observed_count
FROM ingestion_identity
WHERE invalid_source_identity_row_count > 0

ORDER BY severity, alert_id
LIMIT 100;
