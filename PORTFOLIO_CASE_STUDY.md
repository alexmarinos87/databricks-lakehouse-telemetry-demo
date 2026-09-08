# Portfolio Case Study: Databricks Lakehouse Telemetry

## Executive summary

This repository demonstrates a governed Databricks lakehouse for synthetic construction-equipment telemetry and maintenance events. It follows the complete path from immutable source objects through Bronze, Silver and Gold Delta layers into dimensional warehouse facts, forecast evidence, data-quality expectations and repository-controlled reporting assets.

The project is intentionally company-neutral and synthetic. Its purpose is to demonstrate lakehouse engineering decisions and delivery controls without relying on employer, client or production data.

## The engineering problem

Operational telemetry arrives incrementally, can be replayed or corrected and may contain invalid or conflicting event identities. Reporting consumers still need stable business measures, traceable source lineage and a clear distinction between trusted, quarantined and not-yet-committed outputs.

The project therefore needs to answer five questions:

1. How can incremental files be landed without overwriting identities already known to Auto Loader?
2. How should identical replays and conflicting payloads be treated differently?
3. How can operational events become reusable BI dimensions and facts?
4. How can multi-step forecast publication avoid exposing an incomplete run as current?
5. Which controls are source-controlled, and which still require authenticated Databricks or GitHub runtime evidence?

## Architecture

```text
synthetic machine-event CSV files
              ↓
content-addressed incremental/backfill landing identities
              ↓ Auto Loader + persistent checkpoint
Bronze Delta + source, replay and ingestion lineage
              ↓
Silver typed events + invalid/conflicting quarantine
              ↓
Gold uptime · failures · maintenance · parts · client summaries
              ↓
dimensional warehouse facts and shared dimensions
              ↓
versioned forecast histories + manifest-last current views
              ↓
quality checks · Lakeflow expectations · governed reporting SQL
```

The configured workflow orders seven stages: Bronze ingestion, Silver transformation, Gold modelling, warehouse modelling, quality checks, forecast validation and the Lakeflow expectations pipeline.

## Key engineering decisions

### 1. Use immutable object identity for ingestion

Landing filenames contain the full content digest and distinguish normal incremental delivery from intentional backfill. Repeating the same bytes resolves to the same destination, while different content receives a different identity.

The uploader does not request overwrite and verifies existing remote bytes before treating a repeated upload as a no-op.

### 2. Preserve the Auto Loader checkpoint

Incremental delivery and explicit backfill reuse the same checkpoint. The repository does not clear checkpoint or schema state as an ordinary replay mechanism, because stream state, Bronze data and downstream publication need to be assessed together.

### 3. Separate trusted data from quarantine

Silver casts and normalises source fields, checks required business keys and classifies identical replay payloads separately from conflicting payloads sharing an event ID. Invalid and conflicting records are written to `silver_quarantine_machine_events` instead of silently entering trusted Gold outputs.

### 4. Publish business-facing facts and dimensions

The warehouse remodels operational records into shared client, date, machine, model, site and fault dimensions plus daily uptime and event-level failure facts. Reporting assets can therefore consume governed labels and measures without rebuilding joins in every query.

### 5. Make incomplete forecast runs invisible

Forecast rows are written to versioned history tables and reconciled before a publication manifest becomes `COMMITTED`. Current forecast names are views over the latest committed manifest. A newer `STARTED` or `FAILED` run cannot become the selected current publication.

This is a manifest-last visibility boundary rather than a claim of a cross-table Delta transaction.

### 6. Combine executable and declarative quality evidence

The quality notebook checks expected tables, key completeness, uniqueness, metric ranges and output presence. Lakeflow expectations add managed expectation views and an event log for selected Silver, Gold, forecast and publication-manifest contracts.

### 7. Separate deployment from runtime identity and approval

Bundle resources define jobs, pipelines, SQL reporting and Unity Catalog access. Deployment and runtime identities are distinct, while production changes remain behind manual plan/apply and environment approval gates.

Repository configuration and tests demonstrate the intended controls; they do not independently prove that a live workspace currently enforces them.

## Five-minute walkthrough

### 1. Establish the scenario

Start with the synthetic machine-event data and the business outputs: asset uptime, failure events, maintenance cost, parts usage, client summaries and transparent downtime forecasts.

### 2. Show ingestion identity

Open:

```text
scripts/plan_ingestion_upload.py
scripts/upload_ingestion_plan.py
src/lakehouse_demo/ingestion_identity.py
```

Explain how the source digest, delivery mode and optional replay ID determine an immutable landing identity, while the Auto Loader checkpoint remains stable.

### 3. Trace the medallion path

Follow the notebooks in workflow order:

```text
01_bronze_ingest.py
02_silver_transform.py
03_gold_models.py
07_warehouse_model.py
04_quality_checks.py
05_forecast_validation.py
06_lakeflow_quality_expectations.py
```

Call out the Silver quarantine boundary before showing the Gold and warehouse models.

### 4. Explain forecast visibility

Use `05_forecast_validation.py` and the publication-manifest documentation to show why histories are written and reconciled before current views select a run.

### 5. Run local validation

```bash
scripts/run_local_checks.sh
scripts/run_spark_runtime_checks.sh
```

These checks exercise repository contracts and supported local Spark behaviour. They do not replace an authenticated Databricks Runtime execution.

## Evidence map

| Question | Repository evidence |
| --- | --- |
| How does the full lakehouse fit together? | [`docs/architecture.md`](docs/architecture.md) |
| How is a workspace and source landing configured? | [`docs/setup.md`](docs/setup.md) |
| How are bundle deployment and approvals controlled? | [`docs/deployment.md`](docs/deployment.md) |
| How are evidence-producing workflows operated? | [`docs/evidence_workflow_quickstart.md`](docs/evidence_workflow_quickstart.md) |
| Where is forecast publication implemented? | [`notebooks/05_forecast_validation.py`](notebooks/05_forecast_validation.py) |
| Where are declarative expectations defined? | [`notebooks/06_lakeflow_quality_expectations.py`](notebooks/06_lakeflow_quality_expectations.py) |
| Which reporting assets are source-controlled? | [`sql/reporting_assets/manifest.json`](sql/reporting_assets/manifest.json) |
| What risks remain open? | [`docs/engineering_risk_register.md`](docs/engineering_risk_register.md) |
| Where is the complete implementation reference? | [`PROJECT_REFERENCE.md`](PROJECT_REFERENCE.md) |

## Trade-offs and boundaries

- The data is synthetic and demonstrates an operational scenario; it is not client or production telemetry.
- Local checks do not prove Databricks Files API behaviour, live Auto Loader discovery, workspace permissions or a deployed schedule.
- Manifest-last selection prevents incomplete repository-controlled runs from being current, but it is not multi-table transactional atomicity.
- The forecast is deliberately transparent and baseline-oriented; configured accuracy thresholds are required before a validated status is available.
- GitHub configuration and green CI do not by themselves prove effective branch protection, OIDC federation or production approval.

## Interview discussion prompts

- Why use content-addressed objects rather than overwrite-friendly filenames?
- When should a replay reuse a checkpoint, and when would checkpoint recovery become an incident procedure?
- What should happen when the same event ID arrives with different payload content?
- Why publish warehouse facts after Gold rather than reporting directly from operational aggregates?
- What are the limits of manifest-last publication compared with a true atomic transaction?
- Which evidence would be required before describing this as a live governed Databricks deployment?
