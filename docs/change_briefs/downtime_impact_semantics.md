# Change Brief: Define downtime as an outage-impact estimate

## Problem

The repository previously calculated `downtime_pct` as downtime divided by
observed minutes, while also allowing source downtime to exceed source duration.
The quality layer treated ratios above 100 as an unresolved warning. That mixed
two incompatible interpretations: elapsed-time share and operational impact.

## Approved definition

- `duration_minutes` is the observed duration represented by one event.
- `observed_minutes` is the daily sum of event duration.
- running, idle and maintenance minutes are status-specific portions of observed
  duration and must not sum above `observed_minutes`.
- `downtime_minutes` is a non-negative additive estimate of operational outage
  impact attributed to an event.
- downtime impact may overlap the event duration, may be attributed to future
  operational loss, and may exceed either one event's duration or the daily
  observed duration.

## Derived field

The warehouse field is renamed from `downtime_pct` to:

```text
downtime_impact_ratio_pct
```

Formula:

```text
round(downtime_minutes / observed_minutes * 100, 2)
```

The value is null when `observed_minutes` is zero. It is not capped at 100 and
must not be described as availability, elapsed-time share or a mutually
exclusive status percentage.

## Executable controls

- source downtime and duration remain non-negative integers;
- status minutes must remain within observed minutes;
- the impact ratio must exactly reconcile to the approved formula when observed
  minutes are positive;
- the ratio must be null when observed minutes are zero;
- a ratio greater than 100 is valid when the formula supports it;
- the former unresolved semantics warning is removed.

## Compatibility

This is an intentional warehouse schema rename. Consumers of
`fact_machine_uptime_daily.downtime_pct` must adopt
`downtime_impact_ratio_pct`. The Gold `downtime_minutes` measure and forecast
input remain unchanged.

The warehouse notebook uses schema-overwrite publication after the complete
warehouse audit passes. Repository tests cannot prove live Delta schema
migration, downstream dashboard compatibility or external semantic approval.
Review the authenticated development plan and reporting assets before apply.

## Non-goals

- This does not claim source outage-impact estimates are independently accurate.
- It does not decompose downtime into non-overlapping causal intervals.
- It does not change forecast model mathematics.
- It does not cap or normalize downtime impact to observed time.

## Failure and rollback

A negative measure, inconsistent ratio or non-null ratio with zero observed
minutes blocks the quality gate. Source rollback is a revert of the squash
commit. After Databricks execution, restore the previous uptime fact Delta
version and update downstream consumers together before rerunning the warehouse
publication audit.
