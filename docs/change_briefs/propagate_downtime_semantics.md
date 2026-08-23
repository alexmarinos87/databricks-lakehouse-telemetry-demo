# Change Brief: Propagate attributed-downtime semantics into runtime outputs

## Problem

The repository had an accepted attributed-downtime policy and executable helper,
but the actual Gold and warehouse notebooks still called lower-level builders
that did not materialize the new semantic evidence. Quality continued to emit a
warning whenever downtime exceeded observation coverage, even though the accepted
contract explicitly permits that relationship. Reporting also exposed only the
ambiguous legacy label.

This created a policy/runtime split: documentation said high downtime load was
valid, while the published data path did not carry the semantic version or the
preferred measure and downstream controls still treated valid values as suspect.

## Outcome

- Gold and warehouse notebooks use a governed wrapper around the existing
  lower-level builders.
- `gold_machine_uptime` and `fact_machine_uptime_daily` materialize:
  - `downtime_pct` as the compatibility alias;
  - `downtime_load_pct` as the preferred measure;
  - `downtime_exceeds_observed` as explicit evidence;
  - `downtime_semantics_version` as `attributed_incident_v1`.
- The compatibility alias is copied from the canonical load, avoiding duplicate
  formulas.
- Warehouse natural-identity and measure reconciliation includes all semantic
  fields.
- The composite warehouse publication gate rejects missing, partial, corrupted,
  or incorrectly versioned semantic evidence.
- Durable quality checks replace the old high-load warning with a hard
  formula/alias/flag/version contract.
- Lakeflow expectations and SQL reporting expose the same vocabulary without
  imposing a 100% maximum.

## Non-goals

- This change does not deploy the bundle, evolve a live Delta schema, refresh a
  Lakeflow pipeline, update a dashboard, or mutate permissions.
- It does not reinterpret `uptime_pct`; availability and attributed-downtime load
  remain different measures.
- It does not make warehouse publication atomic across all output tables.
- It does not claim external business-owner approval for the synthetic demo
  semantics.
- It does not remove the legacy `downtime_pct` alias.

## Compatibility

The change is additive for Gold and warehouse outputs. Existing consumers may
continue reading `downtime_pct`, whose values remain equal to the previous
formula. New consumers should use `downtime_load_pct` and display explanatory
text that values above 100 are valid.

A partial schema containing only some semantic fields fails closed. A legacy
in-memory test fixture containing none of the new fields may be canonicalized by
the governed wrapper, but persisted governed output must carry the full set.

## Failure and recovery

Repository publication is blocked when:

- required duration values are missing or negative;
- status minutes exceed observation coverage;
- the canonical load formula is wrong;
- the compatibility alias differs from the canonical load;
- the exceedance flag is wrong;
- the semantic version is missing or unexpected;
- Gold and fact semantic values do not reconcile.

Before a live apply, record the current Gold and warehouse Delta versions and
review additive schema changes. Runtime rollback restores the prior Gold and
warehouse versions together, then reruns quality and reporting checks. Source
rollback is a revert of the eventual squash commit.

## Validation plan

- Standard repository contracts and Python compilation.
- Spark tests for normal load, load above 100, zero observation, corruption,
  semantic-version mismatch, Gold-to-fact propagation, quality gating, and
  composite warehouse publication.
- Static notebook tests proving Gold and warehouse use the governed wrappers.
- SQL and Lakeflow source contracts proving the preferred labels and no 0–100
  load bound.

Repository CI cannot prove Databricks Delta schema evolution, effective
permissions, Lakeflow expression support, or downstream dashboard rendering.
Those remain authenticated development-runtime evidence.
