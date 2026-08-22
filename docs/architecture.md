# Architecture

This project demonstrates a compact Databricks Lakehouse pattern using synthetic construction equipment telemetry. It is designed to be easy to explain in an interview without relying on any employer, client or proprietary data.

## Medallion Flow

```text
Immutable CSV objects in cloud storage
  -> Auto Loader with a persistent checkpoint
  -> bronze_machine_events
  -> silver_machine_events
  -> gold_machine_uptime
  -> gold_failure_events
  -> gold_maintenance_costs
  -> gold_parts_usage
  -> gold_client_asset_summary
  -> gold_downtime_forecast_validation_history
  -> gold_downtime_forecast_history
  -> gold_downtime_forecast_publication_manifest
  -> latest-committed forecast views
  -> quality_expectation_* materialized views
  -> quality_expectation_event_log
```

## Bronze

`bronze_machine_events` is populated by Auto Loader from a cloud-file landing directory. The notebook uses `availableNow` so it can be run as a scheduled incremental batch: each run processes files available at start time and stops after the backlog is complete.

Governed uploads use an immutable object identity rather than overwriting a filename already known to Auto Loader:

```text
machine-events__incremental__sha256_<digest>.csv
machine-events__backfill__replay_<replay-id>__sha256_<digest>.csv
```

The planner binds a repository source path, byte length, full SHA-256 digest, destination and the `reuse_existing_checkpoint` policy into a deterministic manifest. The uploader never requests overwrite, verifies an existing destination byte-for-byte before treating it as an idempotent skip, and requires a distinct replay ID for an intentional backfill.

The bronze notebook recursively preflights the bounded landing root, rejects unmanaged object names, explicitly keeps `cloudFiles.allowOverwrites` false and records:

- `_ingested_at`
- `_source_file`
- `_source_object_name`
- `_ingestion_mode`
- `_replay_id`
- `_source_content_sha256`
- `_source_identity_valid`

The bronze layer is intentionally close to source so that downstream assumptions can be audited. Silver event identity remains the row-level replay boundary: an immutable backfill object can be newly discovered by Auto Loader, while identical and conflicting event payloads are still classified downstream.

Auto Loader state is stored outside the Delta table:

- `checkpoint_path` tracks stream progress and processed file identities.
- `schema_location` stores the Auto Loader schema metadata.

Normal incremental delivery and explicit backfill both reuse the existing checkpoint. Repository code does not clear checkpoint or schema state as a replay mechanism. A checkpoint reset is an incident-level recovery decision requiring the Bronze table and downstream state to be assessed together.

By default, the demo uses DBFS paths so it works in a small workspace. For a governed workspace, set `unity_catalog_volume` and the bronze task resolves the raw source, checkpoint and schema metadata paths under `/Volumes/<catalog>/<schema>/<volume>/`. Direct ADLS paths remain available for workspaces that prefer external cloud URI configuration; external uploaders must implement the same immutable identity and no-overwrite contract.

## Silver

`silver_machine_events` applies the main engineering logic:

- Casts timestamps and numeric fields.
- Normalizes categorical values.
- Removes records with missing required business keys.
- Classifies identical replay payloads separately from conflicting payloads sharing an event ID.
- Adds `is_failure_event`.
- Adds a simple operational `health_score`.

Invalid records and conflicting event-ID payloads are written to `silver_quarantine_machine_events`.

## Gold

The gold layer provides BI-ready outputs:

- `gold_machine_uptime`: daily uptime, downtime and health score by asset.
- `gold_failure_events`: failure-level details for reliability analysis.
- `gold_maintenance_costs`: cost and downtime by month, site and model.
- `gold_parts_usage`: part demand by date, site and model.
- `gold_client_asset_summary`: client-facing asset reliability summary.

## Dimensional Warehouse

`07_warehouse_model.py` publishes a compact star schema after the medallion layers. Shared dimensions cover client, date, machine, model and site, while `dim_fault` adds fault code, severity and a sortable severity rank.

The warehouse includes two fact tables:

- `fact_machine_uptime_daily`: daily operating-minute and percentage measures by machine.
- `fact_machine_failure_event`: event-level reliability measures, including downtime, maintenance cost, sensor readings and parts usage.

Saved Databricks SQL assets join these facts to their dimensions, so reporting consumers can use governed business labels without rebuilding joins in every report.

## Forecast Validation And Publication

`05_forecast_validation.py` delegates forecast construction to `src/lakehouse_demo/spark_forecast.py` and publication evidence to `src/lakehouse_demo/spark_forecast_publication.py`.

The baseline uses available observations within prior **calendar-day** windows by site, client and model. Missing dates are not treated as adjacent observations, and the current validation day is excluded from its own baseline. Window membership uses date ordinals rather than elapsed epoch seconds, so daylight-saving transitions do not change which calendar dates are included.

A minimum sample count alone cannot produce `validated_baseline`. The two bundle variables `max_mae_downtime_minutes` and `min_interval_coverage_pct` must both be configured, and both checks must pass. Blank defaults produce `thresholds_not_configured`, preserving a fail-closed distinction between a technical forecast and a client-ready claim. Other states distinguish insufficient history and failed accuracy thresholds.

The workflow passes `job_{{job.run_id}}` into the forecast output so reporting and runtime evidence can identify the Databricks job run that generated a publication.

### Versioned publication

The notebook persists one retry-safe run vintage into:

- `gold_downtime_forecast_validation_history`
- `gold_downtime_forecast_history`

It records the run in `gold_downtime_forecast_publication_manifest`. A run begins as `STARTED`. The notebook replaces only that run's uncommitted history rows, reads the persisted rows back, and verifies row counts, recorded schemas and bounded order-independent payload fingerprints. It changes the manifest to `COMMITTED` only after both histories reconcile.

The governed consumer names remain:

- `gold_downtime_forecast_validation`
- `gold_downtime_forecast`

These are views over the latest `COMMITTED` manifest row. A newer `STARTED` or `FAILED` run is therefore invisible to repository-controlled consumers. This is a **manifest-last visibility boundary**, not a cross-table Delta transaction: the two history writes and manifest update remain separate Delta transactions, but incomplete runs cannot become the selected current publication without the final committed manifest.

A retry of an already committed run is read-only. The notebook reconciles the stored histories against the committed manifest and recreates or alters the views if needed. A retry of an incomplete run replaces only that run's history before attempting the commit again.

The current names must be views. If legacy managed tables already use those names, the notebook fails before any publication write. The operator must preserve and rename the legacy tables using the recovery runbook before enabling versioned publication.

The baseline remains intentionally simple so BI users can see the assumptions and error profile before using forecast output in client-facing narratives.

## Governance And Quality

The `04_quality_checks.py` notebook validates:

- Expected Delta tables exist.
- Silver event IDs are unique.
- Required silver keys are populated.
- Operational metrics are non-negative.
- Gold and warehouse tables contain rows and satisfy their technical invariants.

The results are stored in `quality_check_results` with run-level history.

`06_lakeflow_quality_expectations.py` adds declarative expectations over selected trusted outputs:

- `quality_expectation_silver_machine_events`: required keys, metric ranges and health-score bounds.
- `quality_expectation_gold_machine_uptime`: uptime, downtime and observed-minute checks.
- `quality_expectation_downtime_forecast`: forecast interval, validation-count, threshold-evidence, commit-time and status checks.
- `quality_expectation_forecast_publication_manifest`: publication state, row-count, fingerprint and committed-evidence checks.

The Lakeflow pipeline writes expectation metrics to `quality_expectation_event_log`, which gives a managed event stream for monitoring expectation pass and fail counts.

## Workflow Orchestration

The Databricks bundle configuration deploys the lakehouse pipeline as a workflow job with seven sequential tasks:

1. `bronze_ingest`
2. `silver_transform`
3. `gold_models`
4. `warehouse_model`
5. `quality_checks`
6. `forecast_validation`
7. `quality_expectations_pipeline`

The workflow uses a shared job cluster for notebook tasks and passes the same catalog and schema parameters into each notebook. The bronze task also receives the stable Auto Loader source, checkpoint and schema-location paths. The optional GitHub sample upload chooses an initial or dated increment fixture, an incremental or backfill mode, and a replay ID when required; it creates immutable objects without altering stream state. The forecast task runs after the error-level quality gate and receives configurable calendar window, horizon, minimum-validation and optional accuracy-threshold settings plus the dynamic job-run identifier. The final task refreshes the Lakeflow quality-expectations pipeline.

## Deployment And Access

GitHub Actions runs Dockerized local checks before bundle deployment. The deployment workflow separates validation, bundle plan and deploy stages, with the production target protected by a GitHub environment approval.

The bundle manages least-privilege access for the main Databricks resources:

- Job permissions are defined on `lakehouse_demo_workflow`.
- Pipeline permissions are defined on `lakehouse_quality_expectations`.
- SQL warehouse permissions are defined on `lakehouse_demo_reporting`.
- Unity Catalog schema and volume grants are defined in `resources/access_controls.yml`.

Saved Databricks SQL queries are published after bundle deployment so reporting assets appear under SQL Queries instead of only existing as repository files.

Repository and local Spark tests prove deterministic immutable naming, manifest tamper detection, uploader command construction, ingestion lineage, calendar-window readiness logic, manifest-last selection and existing transformation contracts. They do not prove Files API behaviour, Databricks Runtime execution, Delta multi-table atomicity, workspace permissions or live Auto Loader discovery; those remain authenticated runtime evidence.
