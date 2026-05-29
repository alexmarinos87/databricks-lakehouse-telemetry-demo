# Databricks Lakehouse Demo

Synthetic construction equipment telemetry project for demonstrating a Databricks Lakehouse workflow.

The project is intentionally small, company-neutral and explainable. It shows how raw machine events can move through bronze, silver and gold Delta tables, with validation checks and SQL outputs suitable for reporting.

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
│   └── 06_lakeflow_quality_expectations.py
├── sql/
│   └── gold_reporting_queries.sql
├── data/
│   └── sample_machine_events.csv
├── docs/
│   ├── architecture.md
│   ├── interview_notes.md
│   └── setup.md
├── resources/
│   ├── lakehouse_quality_expectations.yml
│   └── lakehouse_workflow.yml
├── tests/
│   ├── test_azure_ingestion_config.py
│   ├── test_forecast_validation_contract.py
│   ├── test_incremental_ingestion_contract.py
│   ├── test_lakeflow_expectations_contract.py
│   ├── test_quality_history_contract.py
│   ├── test_sample_data_contract.py
│   └── test_unity_catalog_volume_ingestion_contract.py
├── .github/
│   └── workflows/
│       └── ci.yml
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
- GitHub version control and CI validation.

## Lakehouse Layers

| Layer | Table | Purpose |
| --- | --- | --- |
| Bronze | `bronze_machine_events` | Auto Loader incremental ingest of raw CSV-shaped event records |
| Silver | `silver_machine_events` | Typed, cleaned, deduplicated machine events |
| Silver | `silver_quarantine_machine_events` | Invalid records excluded from trusted outputs |
| Gold | `gold_machine_uptime` | Daily uptime, downtime and health by machine |
| Gold | `gold_failure_events` | Failure event details for reliability analysis |
| Gold | `gold_maintenance_costs` | Maintenance cost and downtime aggregates |
| Gold | `gold_parts_usage` | Parts usage by date, site, model and part |
| Gold | `gold_client_asset_summary` | Client-facing asset performance summary |
| Forecast | `gold_downtime_forecast_validation` | Rolling-baseline backtest results with forecast errors |
| Forecast | `gold_downtime_forecast` | Next-horizon downtime forecast with validation status and interval bounds |
| Quality | `quality_expectation_silver_machine_events` | Declarative expectation view over trusted silver records |
| Quality | `quality_expectation_gold_machine_uptime` | Declarative expectation view over machine uptime metrics |
| Quality | `quality_expectation_downtime_forecast` | Declarative expectation view over forecast outputs |
| Quality | `quality_expectation_event_log` | Pipeline event log with expectation metrics |

## How To Run In Databricks

See `docs/setup.md` for the GitHub and Databricks Git folder setup notes.

1. Create or open a Databricks workspace.
2. Create a private GitHub repository and connect it using Databricks Git folders.
3. Clone this repository into the Databricks workspace.
4. Upload `data/sample_machine_events.csv` to the Auto Loader landing directory:
   `dbfs:/FileStore/lakehouse_demo/raw_machine_events/sample_machine_events.csv`.
5. Run the notebooks in order:
   - `01_bronze_ingest.py`
   - `02_silver_transform.py`
   - `03_gold_models.py`
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

## Workflow Job

The repository includes a Databricks bundle workflow configuration:

- `databricks.yml` defines bundle variables, deployment targets and default paths.
- `resources/lakehouse_quality_expectations.yml` defines a Lakeflow Spark Declarative Pipelines resource with expectation-backed materialized views.
- `resources/lakehouse_workflow.yml` defines a scheduled Lakeflow Job with six dependent tasks:
  `bronze_ingest` -> `silver_transform` -> `gold_models` -> `quality_checks` -> `forecast_validation` -> `quality_expectations_pipeline`.

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

`05_forecast_validation.py` demonstrates that pattern with a transparent rolling-mean downtime baseline. It writes backtest rows to `gold_downtime_forecast_validation` and next-horizon forecast rows to `gold_downtime_forecast`, including error metrics, interval bounds, backtest interval coverage and a `forecast_status` flag.

## Declarative Quality Expectations

`06_lakeflow_quality_expectations.py` defines a Lakeflow Spark Declarative Pipelines quality layer over the trusted silver, gold and forecast outputs. The pipeline records expectation metrics for required keys, metric ranges, interval consistency and known forecast status values.

The expectation pipeline is deployed through the Databricks bundle and refreshed as the final task in the workflow job.

## Local Validation

The repository includes a small GitHub Actions workflow and standard-library unit tests:

```bash
python3 -m py_compile notebooks/*.py
python3 -m unittest discover -s tests -v
```

These checks do not replace running the notebooks in Databricks. They catch basic syntax issues and sample-data contract drift before pushing changes.

## Starter Baseline

The repository starts from a compact, reviewable baseline:

- Synthetic sample data with an explicit schema contract.
- Six Databricks notebooks covering Auto Loader bronze ingest, silver transform, gold models, quality checks, forecast validation and declarative quality expectations.
- Databricks bundle configuration for deploying and running the notebook chain as a workflow job.
- Optional Unity Catalog volume-backed raw, checkpoint and schema paths for bronze ingestion.
- Reporting SQL for Databricks SQL or notebook use.
- Lakeflow Spark Declarative Pipelines expectations for selected trusted outputs.
- Transparent forecast validation outputs for client-safe BI narratives.
- Setup, architecture and interview notes.
- GitHub Actions validation for notebook syntax and sample-data drift.

## Interview Summary

This project supports the following explanation:

> I created a small Databricks Lakehouse project using bronze, silver and gold layers, Auto Loader for incremental cloud-file ingestion, Delta tables, validation checks, SQL outputs, a BI-ready gold layer, transparent forecast validation, declarative quality expectations and a Databricks workflow job configuration. I version-controlled the work through GitHub to mirror proper engineering practice.

## Next Improvements

- Add Power BI or Databricks SQL dashboard screenshots using only synthetic data.
- Add unit-style transformation tests with a small PySpark test harness.
