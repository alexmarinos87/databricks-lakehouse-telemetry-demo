# Databricks Lakehouse Demo

Synthetic construction equipment telemetry project for demonstrating a Databricks Lakehouse workflow.

The project is intentionally small, company-neutral and explainable. It shows how raw machine events can move through bronze, silver and gold Delta tables into a dimensional warehouse, with validation checks and SQL outputs suitable for reporting.

## Project Structure

```text
databricks-lakehouse-demo/
├── README.md
├── databricks.yml
├── notebooks/
│   ├── 01_bronze_ingest.py
│   ├── 02_silver_transform.py
│   ├── 03_gold_models.py
│   ├── 04_quality_checks.py
│   ├── 05_forecast_validation.py
│   ├── 06_lakeflow_quality_expectations.py
│   └── 07_warehouse_model.py
├── src/lakehouse_demo/
│   ├── ingestion_identity.py
│   └── spark_ingestion_identity.py
├── sql/
│   └── gold_reporting_queries.sql
├── data/
│   ├── sample_machine_events.csv
│   └── increments/
├── docs/
│   ├── architecture.md
│   ├── deployment.md
│   ├── interview_notes.md
│   └── setup.md
├── resources/
│   ├── access_controls.yml
│   ├── lakehouse_quality_expectations.yml
│   ├── lakehouse_workflow.yml
│   └── sql_reporting.yml
├── scripts/
│   ├── apply_uc_grants.py
│   ├── plan_ingestion_upload.py
│   ├── run_local_checks.sh
│   ├── upload_ingestion_plan.py
│   └── upsert_reporting_queries.py
├── tests/
├── tests_runtime/
├── .github/workflows/
└── .gitignore
```

## Business Scenario

The sample data represents construction equipment telemetry and maintenance events. Each row is an operational event for a machine working at a site. The data includes machine status, fault codes, downtime, maintenance cost, fuel level, temperature, vibration and parts usage.

This is a generic industrial analytics scenario. It does not use confidential, employer, client or production data.

## Portfolio Positioning

This repository is designed as a reusable portfolio project for data engineering, analytics engineering and lakehouse-focused roles. It avoids company-specific wording so it can be discussed with different employers as a general example of:

- Databricks notebook development.
- Medallion architecture.
- Delta table modelling.
- Data quality checks.
- BI-ready gold outputs.
- Dimensional warehouse facts and dimensions.
- Immutable Auto Loader source identity and explicit backfill controls.
- GitHub version control and CI validation.

## Lakehouse Layers

| Layer | Table | Purpose |
| --- | --- | --- |
| Bronze | `bronze_machine_events` | Auto Loader incremental ingest of immutable CSV objects with source digest, mode and replay lineage |
| Silver | `silver_machine_events` | Typed, cleaned, deduplicated machine events |
| Silver | `silver_quarantine_machine_events` | Invalid records excluded from trusted outputs |
| Gold | `gold_machine_uptime` | Daily uptime, downtime and health by machine |
| Gold | `gold_failure_events` | Failure event details for reliability analysis |
| Gold | `gold_maintenance_costs` | Maintenance cost and downtime aggregates |
| Gold | `gold_parts_usage` | Parts usage by date, site, model and part |
| Gold | `gold_client_asset_summary` | Client-facing asset performance summary |
| Warehouse | `dim_client`, `dim_date`, `dim_fault`, `dim_machine`, `dim_model`, `dim_site` | Shared reporting dimensions |
| Warehouse | `fact_machine_uptime_daily` | Daily operating performance by machine |
| Warehouse | `fact_machine_failure_event` | Event-level fault, downtime, cost and parts measures |
| Forecast | `gold_downtime_forecast_validation` | Rolling-baseline backtest results with forecast errors |
| Forecast | `gold_downtime_forecast` | Next-horizon downtime forecast with validation status and interval bounds |
| Quality | `quality_expectation_silver_machine_events` | Declarative expectation view over trusted silver records |
| Quality | `quality_expectation_gold_machine_uptime` | Declarative expectation view over machine uptime metrics |
| Quality | `quality_expectation_downtime_forecast` | Declarative expectation view over forecast outputs |
| Quality | `quality_expectation_event_log` | Pipeline event log with expectation metrics |

## How To Run In Databricks

See `docs/setup.md` for the complete GitHub, Databricks Git folder, immutable upload, incremental and backfill instructions.

1. Create or open a Databricks workspace.
2. Connect the repository using Databricks Git folders.
3. Deploy or create the configured target schema and managed volume.
4. Plan and upload the initial synthetic object without overwrite:

   ```bash
   python3 scripts/plan_ingestion_upload.py \
     --source data/sample_machine_events.csv \
     --destination-root dbfs:/Volumes/main/lakehouse_demo/lakehouse_demo_files/raw_machine_events \
     --mode incremental \
     --output .ingestion/initial-upload-plan.json

   python3 scripts/upload_ingestion_plan.py \
     --target dev \
     --manifest .ingestion/initial-upload-plan.json
   ```

5. Run the notebooks in order:
   - `01_bronze_ingest.py`
   - `02_silver_transform.py`
   - `03_gold_models.py`
   - `07_warehouse_model.py`
   - `04_quality_checks.py`
   - `05_forecast_validation.py`
6. Refresh the Lakeflow quality expectations pipeline from the bundle workflow or by running the deployed pipeline resource.
7. Run the SQL in `sql/gold_reporting_queries.sql` in Databricks SQL or a SQL notebook.

The notebooks default to catalog `main` and schema `lakehouse_demo`. Change the notebook widgets if your workspace uses a different catalog or schema.

For governed file storage, set `unity_catalog_volume` when running the bronze notebook or bundle workflow. The raw source, checkpoint and schema metadata paths then resolve under:

```text
/Volumes/<catalog>/<schema>/<unity_catalog_volume>/
```

The bronze notebook can create the managed volume when `create_unity_catalog_volume` is `true`. If your platform team manages volumes separately, pre-create the volume and set `create_unity_catalog_volume` to `false`.

### Immutable ingestion and backfill

Every governed landing filename contains the complete SHA-256 digest. Repeating the same incremental bytes resolves to the same path even when the local file was renamed, and the uploader verifies the existing remote bytes before reporting a no-op. Different content resolves to a different path.

An intentional replay uses `--mode backfill` and a required replay ID. It creates a new object identity while reusing the existing Auto Loader checkpoint. Repository code never deletes the checkpoint as part of upload or backfill, and the bronze notebook explicitly keeps `cloudFiles.allowOverwrites` disabled.

## Workflow Job

The repository includes a Databricks bundle workflow configuration:

- `databricks.yml` defines bundle variables, deployment targets and default paths.
- `resources/access_controls.yml` defines Unity Catalog schema and volume grants.
- `resources/lakehouse_quality_expectations.yml` defines a Lakeflow Spark Declarative Pipelines resource with expectation-backed materialized views.
- `resources/sql_reporting.yml` defines a small Databricks SQL warehouse for reporting assets.
- `resources/lakehouse_workflow.yml` defines a scheduled Lakeflow Job with seven dependent tasks:
  `bronze_ingest` -> `silver_transform` -> `gold_models` -> `warehouse_model` -> `quality_checks` -> `forecast_validation` -> `quality_expectations_pipeline`.

See `docs/deployment.md` for the GitHub Actions deployment flow, Dockerized validation, production approval gate and least-privilege access model.

The deployment workflow can optionally upload either the initial fixture or the dated increment. It requires an explicit `ingestion_mode`; a backfill additionally requires `backfill_id`. The workflow uses the planner and bounded uploader rather than a fixed destination with `--overwrite`.

The workflow schedule is paused by default. After authenticating the Databricks CLI, validate, deploy and run the workflow from the repository root:

```bash
databricks auth login --host <workspace-url>
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev lakehouse_demo_workflow
```

## Data Quality Checks

The quality notebook checks:

- Required tables can be read.
- Silver `event_id` values are unique.
- Required business keys are populated.
- Operational metrics are non-negative.
- Gold reporting tables contain rows.

Check results are written to `quality_check_results`.

## Forecast Validation

`05_forecast_validation.py` demonstrates a transparent rolling-mean downtime baseline. It writes backtest rows to `gold_downtime_forecast_validation` and next-horizon forecast rows to `gold_downtime_forecast`, including error metrics, interval bounds, backtest interval coverage and a `forecast_status` flag.

## Declarative Quality Expectations

`06_lakeflow_quality_expectations.py` defines a Lakeflow Spark Declarative Pipelines quality layer over the trusted silver, gold and forecast outputs. The pipeline records expectation metrics for required keys, metric ranges, interval consistency and known forecast status values.

The expectation pipeline is deployed through the Databricks bundle and refreshed as the final task in the workflow job.

## Local Validation

The repository includes Dockerized GitHub Actions validation, standard-library tests and a pinned local Spark runtime suite:

```bash
scripts/run_local_checks.sh
scripts/run_spark_runtime_checks.sh
```

These checks do not replace running the notebooks in Databricks. They catch syntax, source contract, immutable-upload, transformation and warehouse drift before deployment.

For an AI-assisted change, use the stricter acceptance gate and generate a review package:

```bash
scripts/run_acceptance_checks.sh
python3 scripts/generate_review_package.py --base origin/main --output .review/review-package.md
```

See the [AI-assisted delivery workflow](docs/ai_delivery_workflow.md) for bounded change briefs, independent/adversarial reviews and the human-only acceptance gate. Current unresolved operational findings are recorded in the [engineering risk register](docs/engineering_risk_register.md). Passing agents and automation provide evidence; they do not authorize merge.

## Starter Baseline

The repository includes:

- Synthetic sample and dated increment data with an explicit schema contract.
- Content-addressed immutable upload planning and bounded remote byte verification.
- Explicit incremental and backfill identities with persistent checkpoint reuse.
- Seven Databricks notebooks covering Auto Loader bronze ingest, silver transform, gold models, dimensional warehouse modelling, quality checks, forecast validation and declarative quality expectations.
- Databricks bundle configuration for deploying and running the notebook chain as a workflow job.
- Optional Unity Catalog volume-backed raw, checkpoint and schema paths for bronze ingestion.
- GitHub Actions deployment with Dockerized validation, plan/apply gates and governed synthetic upload controls.
- Reporting SQL and Lakeflow expectations for selected trusted outputs.
- Local standard-library and executable Spark evidence.

## Interview Summary

> I created a small Databricks Lakehouse project using bronze, silver and gold layers, Auto Loader for incremental cloud-file ingestion, immutable content-addressed source identities, explicit checkpoint-preserving backfills, Delta tables, validation checks, a dimensional warehouse, governed SQL outputs, transparent forecast validation, declarative quality expectations and a Databricks workflow job configuration. I version-controlled the work through GitHub to mirror proper engineering practice.

## Next Improvements

- Add authenticated Databricks plan and development-runtime evidence.
- Add Power BI or Databricks SQL dashboard screenshots using only synthetic data.
