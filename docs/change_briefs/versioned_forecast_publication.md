# Change Brief: Version forecast publication with a committed manifest

## Problem

The forecast notebook currently overwrites validation first and forecast second. A failure between the two writes can expose records from different executions, and every successful run destroys the previous forecast and backtest vintage.

PR #45 adds trustworthy readiness semantics but deliberately leaves this publication gap open.

## Acceptance Criteria

- Persist forecast and validation records by `forecast_run_id` in separate history tables.
- Re-running an incomplete run replaces only that run's history rows.
- A committed retry is read-only and reconciles existing history.
- Record `STARTED` before history mutation and `COMMITTED` only after both persisted histories match the expected row counts, schemas and bounded payload fingerprints.
- Repository-controlled current forecast names resolve only the latest committed run.
- A newer incomplete run cannot change current consumer results.
- Fail before any write when legacy current objects are tables rather than views.
- Add executable Spark evidence for retries, partial history, payload corruption, schema evolution and latest-committed selection.
- Document migration, orphan cleanup and recovery without automatic deletion.

## Non-Goals

- This increment does not provide a transaction spanning the two history tables and manifest.
- It does not automatically rename, drop or migrate a legacy forecast table.
- It does not choose production forecast-accuracy thresholds.
- It does not deploy or run Databricks resources.
- It does not add an automatic history-retention or `VACUUM` policy.
- It does not merge PR #45 or PR #43.

## Architecture Boundaries

- Depends on the forecast schema and run identity introduced by PR #45.
- Shared evidence: `src/lakehouse_demo/spark_forecast_publication.py`.
- Persistence and view orchestration: `notebooks/05_forecast_validation.py`.
- Consumer surfaces: current forecast views, reporting SQL and Lakeflow expectations.
- Recovery procedure: `docs/runbooks/forecast_publication_recovery.md`.

This PR is stacked on `agent/forecast-readiness-thresholds` so its diff contains only publication controls.

## Data, State And Side Effects

### Inputs

- One forecast DataFrame per run and zero or more validation rows.
- Both schemas include `forecast_run_id`, model name, window semantics and baseline window length.

### Persisted state

- `gold_downtime_forecast_history`
- `gold_downtime_forecast_validation_history`
- `gold_downtime_forecast_publication_manifest`

### Current consumer interface

- `gold_downtime_forecast`
- `gold_downtime_forecast_validation`

The current names are Unity Catalog views selecting the latest manifest row with `publication_state = 'COMMITTED'`.

### Retry semantics

- `COMMITTED`: reconcile and return without rewriting history.
- `STARTED`, `FAILED` or unseen: delete only rows for the same safe run ID, append the new candidate, reconcile persisted evidence, then commit.
- A failed attempt remains non-committed and is invisible to the current views.

### Evidence

Each manifest row stores:

- row counts;
- the exact recorded column lists;
- canonical schema SHA-256 values;
- bounded order-independent payload fingerprints;
- start, completion and forecast-generation timestamps.

The payload fingerprint combines aggregate row evidence. It is a reconciliation control, not a claim of adversarial cryptographic proof.

## Security, Permissions And Cost

- No secret, token, identity or new external permission is introduced.
- Analysts retain access to the current views; raw history and the manifest are not added to the analyst table-grant list.
- Admin and engineering access continues through existing schema-level privileges.
- Each publication performs bounded aggregate scans of the two candidate/history slices. Cost scales with the current run's rows, not all historical vintages.
- History storage grows per run until a separately approved retention policy is implemented.

## Failure And Recovery

- A legacy table occupying a current view name fails preflight before state mutation.
- A failure after the `STARTED` row but before commit leaves an incomplete run that current views ignore.
- A persisted count, schema or payload mismatch blocks `COMMITTED`.
- A committed manifest with missing or corrupted history fails closed on retry.
- A failure after manifest commit but before view recreation leaves a reconcilable committed run; retry recreates the views without rewriting history.
- Recovery and migration commands are manual and scoped by exact run ID.

## Validation Plan

- Repository compilation and contracts.
- Existing standard test suite.
- Spark runtime tests covering:
  - incomplete run invisibility;
  - latest committed selection;
  - order-independent retry fingerprints;
  - missing-row detection;
  - same-count payload corruption;
  - later history columns;
  - duplicate manifest rows;
  - zero-row validation history.
- Static checks for manifest-last order, legacy preflight, current-view selection and reporting/expectation integration.
- Databricks bundle validation, Delta writes and Unity Catalog view creation remain unrun until authenticated environment bootstrap is complete.
