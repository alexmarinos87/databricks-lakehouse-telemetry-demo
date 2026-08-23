# Downtime business semantics

## Decision

The repository adopts semantic version `attributed_incident_v1`.

`duration_minutes` is the telemetry interval represented by one event.
`observed_minutes` is the sum of those intervals in a reporting grain.
Running, idle, and maintenance minutes classify observation coverage and must
not sum to more than observed minutes.

`downtime_minutes` is different: it is outage or repair duration attributed to
an incident. It may span beyond the telemetry event that reported the incident,
so it may exceed `observed_minutes`.

## Derived measures

The current `downtime_pct` column is retained as a compatibility alias for:

```text
downtime_load_pct = downtime_minutes / observed_minutes * 100
```

The preferred name is `downtime_load_pct` because the result is a workload
ratio, not a bounded share of observed time. It may exceed 100.

Examples:

| Observed | Attributed downtime | Load | Meaning |
| ---: | ---: | ---: | --- |
| 60 | 15 | 25% | Fifteen attributed outage minutes per observed hour |
| 60 | 120 | 200% | Two attributed outage hours per observed hour; valid for a repair spanning beyond telemetry coverage |
| 0 | 0 | 0% | No observation and no attributed outage |
| 0 | 30 | null | Attributed outage exists but no observation denominator exists |

Use `uptime_pct` for availability. Do not describe `downtime_pct` or
`downtime_load_pct` as the percentage of observation time unavailable.

## Enforced relationships

The executable contract in
`src/lakehouse_demo/spark_downtime_semantics.py` enforces:

- required duration values are populated;
- all duration values are non-negative;
- running + idle + maintenance minutes do not exceed observed minutes;
- the legacy `downtime_pct`, when present, agrees with the canonical load within
  0.01 percentage points.

It intentionally does not reject attributed downtime above observed minutes or
load above 100.

## Repository integration

`src/lakehouse_demo/downtime_pipeline.py` is the governed integration boundary.
The Gold and warehouse notebooks call it rather than calling the lower-level
builders directly.

The integration materializes these fields in both `gold_machine_uptime` and
`fact_machine_uptime_daily`:

```text
downtime_pct
downtime_load_pct
downtime_exceeds_observed
downtime_semantics_version
```

The compatibility alias is written from the canonical load rather than being
calculated by a second independent formula. The warehouse publication gate then
reconstructs and reconciles all four fields from Gold to the fact table.

Quality controls now treat missing, inconsistent, or incorrectly versioned
semantic evidence as an error. A load above 100 is not an error or warning by
itself. Lakeflow expectations enforce the formula, alias, exceedance flag, and
semantic version, and reporting assets use the explicit attributed-downtime
labels.

This source integration is not Databricks runtime evidence. A live migration
still requires an authenticated plan, schema-evolution review, effective grants,
Delta-version evidence, and validation of deployed reports.

## Data-product implications

- Existing fact and reporting columns remain compatible.
- New consumers should expose the preferred `downtime_load_pct` label.
- Forecasting continues to predict attributed `downtime_minutes`, not telemetry
  availability loss.
- Quality alerts distinguish formula/invariant failures from high but
  semantically valid downtime load.
- Comparisons between clients or sites must account for different observation
  coverage; raw attributed downtime alone is not a normalized availability
  measure.

## Migration

The repository source now contains the additive fields and executable gates, but
no automatic live table rewrite is performed. Before deploying the change:

1. Profile zero-observation rows and legacy formula mismatches.
2. Confirm downstream reports do not cap `downtime_pct` at 100.
3. Review the additive Gold and fact schemas in an authenticated bundle plan.
4. Retain the legacy field through a documented compatibility window.
5. Update dashboard labels and explanatory text before removing the alias.
6. Capture pre-change Delta versions and output-schema evidence.
7. Run Gold, warehouse, quality, and reporting validation in development before
   considering production apply.

## Sign-off boundary

This is the repository’s explicit technical/business semantic choice for the
synthetic demo. The governance policy records
`repository_accepted_semantics_pending_external_business_signoff`; it must not
be represented as approval by a real client, employer, or domain owner without a
linked external decision record.

## Rollback

Source rollback is a revert of the eventual squash commit. A deployed schema
rollback must restore the prior query aliases, quality rules, and explanatory
labels together. Do not restore a 0–100 bound on attributed downtime without
also changing the underlying business definition and forecast target.
