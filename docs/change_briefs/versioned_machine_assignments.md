# Change Brief: Version machine assignments without unknown-member facts

## Problem

The warehouse previously treated every machine-to-client/site/model assignment
change as a global conflict. That blocked legitimate reassignment on a later
calendar date. Facts were also assembled through a chain of inner joins, so a
future dimension-construction defect could remove rows before publication.

## Decision

Required business identities remain mandatory. The warehouse does not invent
`UNKNOWN`, zero, null, or other sentinel members for required client, site,
machine, model, date, event, fault, or severity values.

A machine may change client, site, or model on a later date. `dim_machine`
therefore uses dated assignment versions:

```text
machine_key
machine_id
site_id
client_id
model
assignment_version
valid_from_date
valid_to_date
is_current
```

The surrogate key is deterministic from `machine_id` and `valid_from_date`.
Facts resolve the assignment whose validity interval contains `event_date` and
whose client/site/model identity matches the source row.

Two different assignments for the same machine on the same `event_date` remain
a hard conflict because the source does not contain a finer assignment-effective
timestamp.

## No-silent-loss boundary

- Required identities are checked before dimension construction.
- Client, site, model, date, and fault keys are derived directly from validated
  source identities rather than through row-dropping inner joins.
- Machine assignment resolution uses a left join and checks unmatched and total
  row counts before fact construction.
- Existing source-to-fact, grain, referential, natural-identity, and measure
  audits still run before the first Delta write.

A late or incomplete required dimension identity blocks the warehouse run. It
is not converted to an unknown member and is not silently omitted.

## Compatibility

- `dim_machine` changes from one row per machine to one row per dated assignment
  version.
- `machine_key` changes because it now includes `valid_from_date`.
- Fact natural identity and business measures are unchanged.
- Consumers that treat `dim_machine` as current-only must filter
  `is_current = true`; historical fact joins continue through `machine_key`.
- Existing warehouse tables are overwritten by the notebook after the complete
  publication audit passes, so all dimensions and facts change together in the
  same workflow task, but not in one cross-table transaction.

## Non-goals

- This is not a general-purpose master-data SCD2 process.
- It does not accept retroactive overlapping assignment intervals from a
  separate source system.
- It does not create unknown members for optional descriptive attributes.
- It does not make warehouse publication cross-table ACID.

## Failure and rollback

Same-day contradictions, blank required identities, unmatched assignment
versions, or row-count changes fail before publication. Source rollback is a
revert of the squash commit. After Databricks execution, restore the previous
Delta versions of dimensions and facts together, then rerun all warehouse
reconciliation queries.

## Evidence

Executable Spark scenarios cover:

- a clean static assignment;
- two dated versions for one machine;
- correct validity boundaries and fact keys;
- a same-day contradictory assignment;
- missing required identity rejection;
- failure-only machines;
- unchanged source-to-fact counts.
