WITH latest_quality_run AS (
  SELECT
    quality_run_id,
    checked_at,
    failed_error_check_count,
    failed_warning_check_count,
    all_error_checks_passed
  FROM main.lakehouse_demo.quality_metric_history
  ORDER BY checked_at DESC
  LIMIT 1
)
SELECT
  latest.quality_run_id,
  latest.checked_at,
  latest.failed_error_check_count,
  latest.failed_warning_check_count,
  latest.all_error_checks_passed,
  q.severity,
  q.check_name,
  q.status,
  q.detail,
  q.observed_count
FROM latest_quality_run AS latest
LEFT JOIN main.lakehouse_demo.quality_check_results AS q
  ON q.quality_run_id = latest.quality_run_id
  AND q.status = 'fail'
ORDER BY
  CASE q.severity
    WHEN 'error' THEN 1
    WHEN 'warning' THEN 2
    ELSE 3
  END,
  q.check_name;
