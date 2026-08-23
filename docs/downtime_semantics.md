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

## Data-product implications

- Existing fact and reporting columns remain compatible.
- New consumers should expose the preferred `downtime_load_pct` label.
- Forecasting continues to predict attributed `downtime_minutes`, not telemetry
  availability loss.
- Quality alerts should distinguish formula/invariant failures from high but
  semantically valid downtime load.
- Comparisons between clients or sites must account for different observation
  coverage; raw attributed downtime alone is not a normalized availability
  measure.

## Migration

No automatic live table rewrite is included. Before adding the preferred alias
to deployed tables or reports:

1. Profile zero-observation rows and legacy formula mismatches.
2. Confirm downstream reports do not cap `downtime_pct` at 100.
3. Add `downtime_load_pct` as an additive field or query alias.
4. Retain the legacy field through a documented compatibility window.
5. Update dashboard labels and explanatory text before removing the alias.
6. Capture pre-change Delta versions and output-schema evidence.

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
