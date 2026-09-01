# Databricks Lakehouse Telemetry Demo

A portfolio-grade Databricks lakehouse for synthetic construction-equipment telemetry and maintenance data. It demonstrates governed ingestion, medallion transformations, dimensional modelling, quality evidence, analytical reporting, and repeatable source-controlled delivery.

**Databricks · PySpark · Delta Lake · Auto Loader · Unity Catalog · Asset Bundles · GitHub Actions**

## At a glance

| Area | Implementation focus |
| --- | --- |
| Ingestion | Immutable, content-addressed files with Auto Loader checkpoints and explicit incremental/backfill identities |
| Transformation | Typed Bronze, trusted and quarantined Silver, and BI-ready Gold datasets |
| Modelling | Shared dimensions with machine-uptime and failure-event fact tables |
| Quality | Executable checks, persisted evidence, Lakeflow expectations, and forecast validation |
| Analytics | Repository-controlled SQL assets for reliability, faults, parts usage, forecasting, and quality monitoring |
| Governance | Unity Catalog controls, distinct deployment/runtime identities, and fail-closed evidence contracts |
| Delivery | Databricks Asset Bundles, dependent workflow tasks, Dockerised validation, and manual plan/apply gates |

## Architecture

```text
synthetic machine events
        ↓
immutable content-addressed landing objects
        ↓ Auto Loader
Bronze Delta + source and replay lineage
        ↓
typed Silver + invalid-record quarantine
        ↓
versioned Gold models + dimensional warehouse
        ↓
quality evidence · forecasts · governed reporting SQL
```

## What makes this more than a notebook demo

- Landing identities are derived from complete source digests rather than mutable filenames.
- Normal incremental loads and intentional backfills are separate, auditable operations.
- Replays preserve the Auto Loader checkpoint and do not enable source overwrites.
- Trusted and quarantined records are separated before Gold publication.
- Silver, Gold, forecast, and warehouse visibility is committed through versioned publication manifests.
- Operational records are remodelled into reusable dimensions and facts.
- Analyst-facing SQL assets expose reliability, recurrence, concentration, forecasting, and quality evidence.
- Standard-library and pinned Spark-runtime suites detect source and execution drift before deployment.
- Automation produces evidence; human review remains the merge and deployment authority.

## Validate locally

```bash
scripts/run_local_checks.sh
scripts/run_spark_runtime_checks.sh
```

To connect the project to a Databricks workspace and run its governed workflow, start with [`docs/setup.md`](docs/setup.md).

## Explore the evidence

| Topic | Starting point |
| --- | --- |
| End-to-end architecture | [`docs/architecture.md`](docs/architecture.md) |
| Workspace and ingestion setup | [`docs/setup.md`](docs/setup.md) |
| Bundle and deployment controls | [`docs/deployment.md`](docs/deployment.md) |
| Evidence workflow | [`docs/evidence_workflow_quickstart.md`](docs/evidence_workflow_quickstart.md) |
| Forecast validation | [`notebooks/05_forecast_validation.py`](notebooks/05_forecast_validation.py) |
| Lakeflow expectations | [`notebooks/06_lakeflow_quality_expectations.py`](notebooks/06_lakeflow_quality_expectations.py) |
| Reporting asset catalogue | [`sql/reporting_assets/manifest.json`](sql/reporting_assets/manifest.json) |
| Bundle resources | [`resources/`](resources/) |
| Current engineering risks | [`docs/engineering_risk_register.md`](docs/engineering_risk_register.md) |
| Complete implementation reference | [`PROJECT_REFERENCE.md`](PROJECT_REFERENCE.md) |

## Data and evidence boundary

All business data in this repository is synthetic and company-neutral. The project demonstrates source-controlled architecture, executable local contracts, and Databricks deployment configuration; it does not claim that a production workspace or client system has been operated through this repository.

Source-controlled gates and green CI do not prove effective branch protection, live OIDC federation, or a Databricks runtime deployment. Those remain separate external and runtime evidence boundaries.
