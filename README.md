# Databricks Lakehouse Demo

Synthetic construction equipment telemetry project for demonstrating a Databricks Lakehouse workflow.

The project is intentionally small and explainable. It shows how raw machine events can move through bronze, silver and gold Delta tables, with validation checks and SQL outputs suitable for reporting.

## Project Structure

```text
databricks-lakehouse-demo/
├── README.md
├── notebooks/
│   ├── 01_bronze_ingest.py
│   ├── 02_silver_transform.py
│   ├── 03_gold_models.py
│   └── 04_quality_checks.py
├── sql/
│   └── gold_reporting_queries.sql
├── data/
│   └── sample_machine_events.csv
├── docs/
│   ├── architecture.md
│   ├── interview_notes.md
│   └── setup.md
└── .gitignore
```

## Business Scenario

The sample data represents construction equipment telemetry and maintenance events. Each row is an operational event for a machine working at a site. The data includes machine status, fault codes, downtime, maintenance cost, fuel level, temperature, vibration and parts usage.

This is a generic industrial analytics scenario. It does not use confidential, employer, client or production data.

## Lakehouse Layers

| Layer | Table | Purpose |
| --- | --- | --- |
| Bronze | `bronze_machine_events` | Raw CSV-shaped event records with ingestion metadata |
| Silver | `silver_machine_events` | Typed, cleaned, deduplicated machine events |
| Silver | `silver_quarantine_machine_events` | Invalid records excluded from trusted outputs |
| Gold | `gold_machine_uptime` | Daily uptime, downtime and health by machine |
| Gold | `gold_failure_events` | Failure event details for reliability analysis |
| Gold | `gold_maintenance_costs` | Maintenance cost and downtime aggregates |
| Gold | `gold_parts_usage` | Parts usage by date, site, model and part |
| Gold | `gold_client_asset_summary` | Client-facing asset performance summary |

## How To Run In Databricks

See `docs/setup.md` for the GitHub and Databricks Git folder setup notes.

1. Create or open a Databricks workspace.
2. Create a private GitHub repository and connect it using Databricks Git folders.
3. Clone this repository into the Databricks workspace.
4. Upload `data/sample_machine_events.csv` to `dbfs:/FileStore/lakehouse_demo/sample_machine_events.csv`.
5. Run the notebooks in order:
   - `01_bronze_ingest.py`
   - `02_silver_transform.py`
   - `03_gold_models.py`
   - `04_quality_checks.py`
6. Run the SQL in `sql/gold_reporting_queries.sql` in Databricks SQL or a SQL notebook.

The notebooks default to catalog `main` and schema `lakehouse_demo`. Change the notebook widgets if your workspace uses a different catalog or schema.

## Data Quality Checks

The quality notebook checks:

- Required tables can be read.
- Silver `event_id` values are unique.
- Required business keys are populated.
- Operational metrics are non-negative.
- Gold reporting tables contain rows.

Check results are written to `quality_check_results`.

## Interview Summary

This project supports the following explanation:

> I created a small Databricks Lakehouse project using bronze, silver and gold layers, Delta tables, incremental ingestion concepts, validation checks, SQL outputs and a BI-ready gold layer. I version-controlled the work through GitHub to mirror proper engineering practice.

## Next Improvements

- Convert the bronze ingest to Auto Loader for incremental cloud-file ingestion.
- Add Delta Live Tables expectations.
- Add workflow job configuration.
- Add Power BI or Databricks SQL dashboard screenshots using only synthetic data.
- Add unit-style transformation tests with a small PySpark test harness.
