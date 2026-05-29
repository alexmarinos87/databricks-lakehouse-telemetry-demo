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
