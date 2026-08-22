# Change Brief: Require evidence-based forecast readiness

## Problem

The downtime forecast currently labels a segment `validated_baseline` when it only has a minimum number of backtest rows. It does not require an accepted accuracy or interval-coverage result. Its `baseline_window_days` implementation also uses preceding rows, so observations separated by long calendar gaps can still be treated as adjacent days.

This can overstate the business meaning of the output and make sparse source history look more recent than it is.

## Acceptance Criteria

- Forecast validation uses prior **calendar-day** ranges and excludes the current observation from its own baseline.
- Forecast generation uses observations within the configured latest calendar-day window rather than the latest N rows.
- `validated_baseline` is impossible unless both accuracy thresholds are explicitly configured and met.
- Partial threshold configuration fails before any output write.
- Distinct states identify insufficient history, missing thresholds and failed thresholds.
- The job passes a traceable Databricks job-run identifier into the forecast output.
- Reporting and Lakeflow expectations expose and validate the threshold evidence.
- Local PySpark tests execute sparse-date, clean-baseline, failed-threshold and insufficient-history scenarios.

## Non-Goals

- This increment does not retain forecast vintages or make the two current forecast-table overwrites atomic.
- It does not define organization-approved production thresholds; blank defaults intentionally prevent a client-ready claim.
- It does not replace the transparent rolling-mean baseline with a more complex model.
- It does not deploy the bundle, execute the workflow or mutate Databricks data.

## Architecture Boundaries

- Shared transformation: `src/lakehouse_demo/spark_forecast.py`.
- Databricks adapter and persistence: `notebooks/05_forecast_validation.py`.
- Workflow parameters: `databricks.yml` and `resources/lakehouse_workflow.yml`.
- Consumer contracts: forecast SQL and Lakeflow expectations.
- Existing table names and the overwrite publication mode remain compatible.

## Data, State And Side Effects

- Input grain: one or more Gold uptime rows grouped to `(event_date, site_id, client_id, model)` daily actuals.
- Validation window: prior configured calendar days, ending one day before the current observation.
- Forecast window: available observations from the latest actual date back through the configured calendar span.
- Output grains remain one validation row per observed segment/day and one forecast row per segment.
- The current forecast and validation tables are still overwritten by the notebook.
- No checkpoint, source-file or replay behavior changes in this increment.

## Security, Permissions And Cost

- No new identity, secret or permission is introduced.
- `forecast_run_id` is Databricks-generated job metadata, not a credential.
- Local Spark coverage adds a small bounded CI cost. Databricks runtime cost remains unchanged apart from modest additional expressions and columns when the workflow is eventually run.

## Failure And Recovery

- Missing required Gold columns, empty dated input, invalid window values, unsafe run IDs, malformed UTC timestamps and partial/invalid thresholds fail before writes.
- The notebook still writes validation before forecast. A failure between those writes can expose a mixed current publication; that existing risk is explicitly deferred to the versioned-publication increment.
- Source rollback is a revert of the PR. Data rollback uses the previous Delta table versions if this change has already been executed in Databricks.

## Validation Plan

- Standard repository compilation, contracts and unit tests.
- Executable PySpark tests under the pinned local Spark runtime.
- Static checks that the notebook delegates to shared logic before writes.
- Static checks for the Databricks `{{job.run_id}}` dynamic value reference and threshold propagation.
- Databricks bundle validation and runtime execution remain unrun until authenticated environment bootstrap is complete.
