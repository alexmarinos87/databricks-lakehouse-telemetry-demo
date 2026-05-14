# Interview Notes

## Project Pitch

I built a small, company-neutral Databricks Lakehouse project using synthetic construction equipment telemetry. The project follows a bronze, silver and gold architecture, with Auto Loader-based raw ingestion, typed and deduplicated transformations, validation checks, and BI-ready Delta tables for uptime, failure events, maintenance costs, parts usage and client asset summaries.

## Why This Is Relevant

The scenario mirrors common industrial data problems:

- Equipment sends operational events from different sites.
- Raw data needs quality checks before it can be trusted.
- Business users need simple aggregate outputs, not raw event streams.
- Engineering work should be version controlled and reproducible.

## What I Would Emphasize

- Bronze uses Auto Loader to incrementally ingest new CSV files while preserving source-shaped records with lineage metadata.
- Silver applies data quality rules, typing and deduplication.
- Gold translates engineering data into reporting views.
- Delta tables provide reliable managed storage for analytics.
- GitHub version control makes the work reviewable and maintainable.

## Safe Boundaries

This demo uses synthetic data only. It does not include internal company data, client names, production architecture, confidential screenshots, commercial costs or proprietary code.

It can be discussed with different employers because the business scenario is generic industrial telemetry rather than a replica of any specific company's internal systems.

## Possible Talking Points

- How Auto Loader checkpoints support incremental cloud-file ingestion.
- How Unity Catalog could govern access to tables and schemas.
- How expectations could be extended with a framework such as DLT expectations or Great Expectations.
- How gold tables could feed Power BI dashboards or Databricks SQL dashboards.
- How the model could expand toward predictive maintenance once more historical data exists.
