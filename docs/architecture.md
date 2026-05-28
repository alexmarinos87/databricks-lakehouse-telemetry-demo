# Architecture

This project demonstrates a compact Databricks Lakehouse pattern using synthetic construction equipment telemetry. It is designed to be easy to explain in an interview without relying on any employer, client or proprietary data.

## Medallion Flow

```text
CSV files in cloud storage
  -> Auto Loader
  -> bronze_machine_events
  -> silver_machine_events
  -> gold_machine_uptime
  -> gold_failure_events
  -> gold_maintenance_costs
  -> gold_parts_usage
  -> gold_client_asset_summary
  -> gold_downtime_forecast_validation
  -> gold_downtime_forecast
  -> quality_expectation_* materialized views
  -> quality_expectation_event_log
```

## Bronze

`bronze_machine_events` is populated by Auto Loader from a cloud-file landing directory. The notebook uses `availableNow` so it can be run as a scheduled incremental batch: each run processes files available at start time and stops after the backlog is complete.

The table stores the raw CSV-shaped records and adds ingestion metadata:

- `_ingested_at`
- `_source_file`

The bronze layer is intentionally close to source so that downstream assumptions can be audited.

Auto Loader state is stored outside the Delta table:

- `checkpoint_path` tracks stream progress and processed files.
- `schema_location` stores the Auto Loader schema metadata.

## Silver

`silver_machine_events` applies the main engineering logic:

- Casts timestamps and numeric fields.
- Normalizes categorical values.
- Removes records with missing required business keys.
- Deduplicates on `event_id`.
- Adds `is_failure_event`.
- Adds a simple operational `health_score`.

Invalid records are written to `silver_quarantine_machine_events`.

## Gold

The gold layer provides BI-ready outputs:

- `gold_machine_uptime`: daily uptime, downtime and health score by asset.
- `gold_failure_events`: failure-level details for reliability analysis.
- `gold_maintenance_costs`: cost and downtime by month, site and model.
- `gold_parts_usage`: part demand by date, site and model.
- `gold_client_asset_summary`: client-facing asset reliability summary.

## Forecast Validation

`05_forecast_validation.py` adds a transparent baseline forecasting layer on top of `gold_machine_uptime`.

The notebook creates:

- `gold_downtime_forecast_validation`: historical rolling-baseline predictions with actual downtime, forecast error fields and interval coverage flags.
- `gold_downtime_forecast`: next-horizon downtime forecasts with validation metrics, interval bounds, backtest interval coverage and a readiness status.

The baseline uses recent daily downtime by site, client and model. It is intentionally simple so that BI users can see the assumptions and error profile before using forecast output in client-facing narratives.

## Governance And Quality

The `04_quality_checks.py` notebook validates:

- Expected Delta tables exist.
- Silver event IDs are unique.
- Required silver keys are populated.
- Operational metrics are non-negative.
- Gold tables contain rows.

The results are stored in `quality_check_results`, giving a simple audit surface for the pipeline.

`06_lakeflow_quality_expectations.py` adds declarative expectations over selected trusted outputs:

- `quality_expectation_silver_machine_events`: required keys, metric ranges and health-score bounds.
- `quality_expectation_gold_machine_uptime`: uptime, downtime and observed-minute checks.
- `quality_expectation_downtime_forecast`: forecast interval, validation-count and status checks.

The Lakeflow pipeline writes expectation metrics to `quality_expectation_event_log`, which gives a managed event stream for monitoring expectation pass and fail counts.

## Workflow Orchestration

The Databricks bundle configuration deploys the lakehouse pipeline as a workflow job with six sequential tasks:

1. `bronze_ingest`
2. `silver_transform`
3. `gold_models`
4. `quality_checks`
5. `forecast_validation`
6. `quality_expectations_pipeline`

The workflow uses a shared job cluster for notebook tasks and passes the same catalog and schema parameters into each notebook. The bronze task also receives the Auto Loader source, checkpoint and schema-location paths. The forecast task runs after the error-level quality gate and receives configurable baseline window, horizon and minimum-validation settings. The final task refreshes the Lakeflow quality-expectations pipeline.
