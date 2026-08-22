# Forecast Publication Recovery

Use this runbook for the versioned downtime forecast publication introduced after PR #45. Run commands only in the intended target catalog and schema.

## Safety Rules

- Do not delete a committed run unless a separately reviewed data-retention or rollback decision explicitly identifies it.
- Never delete or reset Auto Loader checkpoints as part of forecast recovery.
- Never drop the publication manifest to make a failed run disappear.
- Record the exact catalog, schema, `forecast_run_id`, workflow run and operator before changing state.
- Review row counts and fingerprints before promoting or deleting anything.

## Objects

```text
gold_downtime_forecast_validation_history
gold_downtime_forecast_history
gold_downtime_forecast_publication_manifest
gold_downtime_forecast_validation       -- latest-COMMITTED view
gold_downtime_forecast                  -- latest-COMMITTED view
```

A current view selects only the latest manifest row in `COMMITTED` state. `STARTED` and `FAILED` runs are retained as operational evidence but remain invisible to governed current consumers.

## 1. Inspect Publication State

```sql
SELECT
  forecast_run_id,
  publication_state,
  publication_started_at_utc,
  publication_completed_at_utc,
  forecast_row_count,
  validation_row_count,
  forecast_schema_sha256,
  validation_schema_sha256,
  forecast_payload_sha256,
  validation_payload_sha256
FROM <catalog>.<schema>.gold_downtime_forecast_publication_manifest
ORDER BY publication_started_at_utc DESC, forecast_run_id DESC;
```

Confirm the current run:

```sql
SELECT DISTINCT forecast_run_id, publication_completed_at_utc
FROM <catalog>.<schema>.gold_downtime_forecast;

SELECT DISTINCT forecast_run_id, publication_completed_at_utc
FROM <catalog>.<schema>.gold_downtime_forecast_validation;
```

Both current views must resolve to the same committed run. The validation view can contain zero rows when no backtest history exists; use the manifest to confirm its zero-row evidence.

## 2. Recover An Incomplete Run

For an exact `STARTED` or `FAILED` run, prefer rerunning the same Databricks job repair so the same `job.run_id` is supplied.

The notebook will:

1. replace only that run's validation history;
2. replace only that run's forecast history;
3. compare persisted evidence with the candidate;
4. update the manifest to `COMMITTED` only when both match.

Do not manually set a manifest row to `COMMITTED`. The notebook's read-back reconciliation is the promotion gate.

## 3. Clean Up An Abandoned Incomplete Run

Use this only after confirming the run is not `COMMITTED`, no workflow repair will reuse it, and a retained manifest record is sufficient evidence.

```sql
DELETE FROM <catalog>.<schema>.gold_downtime_forecast_validation_history
WHERE forecast_run_id = '<exact-safe-run-id>';

DELETE FROM <catalog>.<schema>.gold_downtime_forecast_history
WHERE forecast_run_id = '<exact-safe-run-id>';

UPDATE <catalog>.<schema>.gold_downtime_forecast_publication_manifest
SET publication_state = 'FAILED',
    publication_completed_at_utc = '<YYYY-MM-DDTHH:MM:SSZ>'
WHERE forecast_run_id = '<exact-safe-run-id>'
  AND publication_state = 'STARTED';
```

Re-query the manifest and current views. Current consumer results must remain on the previous committed run.

## 4. Migrate Legacy Current Tables

The notebook refuses to replace an existing managed table with a view. Preserve the legacy data first.

Choose unique backup names and verify they do not already exist:

```sql
ALTER TABLE <catalog>.<schema>.gold_downtime_forecast_validation
RENAME TO <catalog>.<schema>.gold_downtime_forecast_validation_legacy_<date>;

ALTER TABLE <catalog>.<schema>.gold_downtime_forecast
RENAME TO <catalog>.<schema>.gold_downtime_forecast_legacy_<date>;
```

Record the pre-change Delta versions:

```sql
DESCRIBE HISTORY <catalog>.<schema>.gold_downtime_forecast_validation_legacy_<date>;
DESCRIBE HISTORY <catalog>.<schema>.gold_downtime_forecast_legacy_<date>;
```

Run a plan and the forecast task only after the backup objects and target names have been reviewed. The notebook creates the latest-committed views after a successful manifest commit.

Do not drop the legacy backups until the new current views, reporting queries, expectations and permissions have all been validated.

## 5. Recover A Committed Run With Corrupt Or Missing History

A committed retry fails closed if manifest evidence no longer matches history.

1. Stop new forecast runs.
2. Identify whether the history table was modified, restored or partially deleted.
3. Compare Delta history for both forecast history tables.
4. Restore the affected table to the version that contains the committed run, or create a new run rather than editing the committed manifest.
5. Rerun the reconciliation through the notebook.
6. Confirm the current views resolve to one healthy committed run.

Do not downgrade a committed manifest to `STARTED` merely to bypass reconciliation.

## 6. Roll Back The Current Publication

The safest rollback is a new reviewed manifest commit referencing a rebuilt, reconciled run. The source implementation does not provide an unaudited “flip current” command.

For an emergency source rollback:

1. revert the repository commit;
2. preserve the history and manifest tables;
3. do not recreate legacy overwrite tables over the current view names without an explicit migration plan;
4. validate reporting and Lakeflow expectations before resuming schedules.

## Closure Evidence

Record:

- target catalog and schema;
- affected run ID and job-run URL;
- manifest row before and after;
- history row counts;
- relevant Delta versions;
- current-view run IDs;
- operator and reviewer;
- whether any incomplete history was deleted;
- whether legacy backup tables remain.
