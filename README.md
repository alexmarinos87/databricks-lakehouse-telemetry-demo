# Databricks Lakehouse Telemetry Demo

A portfolio-grade Databricks lakehouse for synthetic construction-equipment telemetry and maintenance data, designed to demonstrate governed ingestion, medallion transformations, dimensional modelling, data quality and repeatable delivery.

**Databricks · PySpark · Delta Lake · Auto Loader · Unity Catalog · Asset Bundles · GitHub Actions**

## At a glance

| Area | Implementation focus |
| --- | --- |
| Ingestion | Immutable, content-addressed files with Auto Loader checkpoints and explicit incremental/backfill identities |
| Transformation | Typed Bronze, trusted and quarantined Silver, and BI-ready Gold datasets |
| Modelling | Shared dimensions with machine-uptime and failure-event fact tables |
| Quality | Executable checks, quarantine rules, Lakeflow expectations and forecast-validation evidence |
| Governance | Unity Catalog schema and volume controls with explicit grants |
| Delivery | Databricks Asset Bundles, dependent workflow tasks, Dockerised validation and guarded GitHub Actions deployment |

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
Gold operational models + dimensional warehouse
        ↓
quality expectations · forecast validation · reporting SQL
```

## What makes this more than a notebook demo

- Landing identities are derived from the full source digest rather than mutable filenames.
- Normal incremental loads and intentional backfills are distinct, auditable operations.
- Replays preserve the Auto Loader checkpoint and do not enable source overwrites.
- Trusted and quarantined records are separated before Gold outputs are produced.
- Operational tables are remodelled into reusable dimensions and facts for reporting.
- Local standard-library and pinned Spark-runtime checks catch contract drift before a Databricks deployment.
- Automation produces review evidence but does not replace the human merge or production-approval decision.

## Validate locally

```bash
scripts/run_local_checks.sh
scripts/run_spark_runtime_checks.sh
```

To connect the project to a Databricks workspace and run the bundle workflow, start with [`docs/setup.md`](docs/setup.md).

## Explore the evidence

| Topic | Starting point |
| --- | --- |
| End-to-end architecture | [`docs/architecture.md`](docs/architecture.md) |
| Workspace and ingestion setup | [`docs/setup.md`](docs/setup.md) |
| Bundle and deployment controls | [`docs/deployment.md`](docs/deployment.md) |
| Reporting queries | [`sql/gold_reporting_queries.sql`](sql/gold_reporting_queries.sql) |
| Bundle resources | [`resources/`](resources/) |
| AI-assisted delivery controls | [`docs/ai_delivery_workflow.md`](docs/ai_delivery_workflow.md) |
| Complete project walkthrough | [`PROJECT_REFERENCE.md`](PROJECT_REFERENCE.md) |

## Data and scope boundary

All business data in this repository is synthetic and company-neutral. The project demonstrates executable local contracts and Databricks deployment configuration; it does not claim that a production workspace or client system has been operated through this repository.
