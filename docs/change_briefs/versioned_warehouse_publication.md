# Change Brief: Version the warehouse behind one committed manifest

## Problem

The warehouse notebook audits the star schema before writing, but then overwrites
six dimensions and two facts independently. An interruption can expose valid
surrogate keys from one generation beside facts or dimensions from another.
Row-count, referential, natural-identity, measure, assignment, and downtime
semantic checks cannot prevent a mixed state after the first overwrite succeeds.

## Outcome

Publish all eight warehouse relations as one versioned family:

```text
dim_client_history
dim_date_history
dim_fault_history
dim_machine_history
dim_model_history
dim_site_history
fact_machine_failure_event_history
fact_machine_uptime_daily_history
warehouse_publication_manifest
```

The eight original consumer names become current views that select one latest
`COMMITTED` `warehouse_publication_run_id`.

## Acceptance Criteria

- The existing composite warehouse audit passes before publication evidence is
  constructed.
- The workflow supplies
  `warehouse_publication_run_id = job_{{job.run_id}}`.
- The exact set of six dimensions and two facts is required.
- Every candidate and history row carries one warehouse run identity.
- Candidate evidence records row count, canonical schema, and bounded
  order-independent payload fingerprint for every relation.
- A `STARTED` manifest precedes run-scoped history replacement.
- All eight persisted histories reconcile before `COMMITTED`.
- `COMMITTED` is the final visibility write.
- A non-committed retry replaces only its own eight history slices.
- A committed retry is read-only and reconciles all histories before repairing
  view definitions.
- Existing views use `ALTER VIEW ... AS`; absent views use `CREATE VIEW`.
- Legacy physical dimensions or facts fail before publication mutation.
- Analyst grants target current warehouse views, never histories or the
  manifest.
- Spark tests prove all-eight switching, partial-generation isolation,
  same-count fact corruption, and missing-dimension detection.

## Non-Goals

- This is manifest-last visibility, **not cross-table ACID** across eight history
  tables and one manifest.
- It does not change surrogate-key, assignment-history, unknown-member,
  downtime, or fact-grain semantics.
- It does not deploy, migrate live tables, execute the workflow, or alter grants.
- It does not expose warehouse histories to analysts.
- It does not define automatic retention or `VACUUM` policy.

## Compatibility And Migration

The eight current names change from physical tables to views. The notebook
refuses implicit table replacement. Before first runtime use, an operator must
preserve and rename every legacy dimension and fact, record Delta versions,
schemas, row counts, ownership, grants, fact control totals, and dimension
membership, then run one governed warehouse generation.

Every current view adds:

```text
warehouse_publication_run_id
publication_completed_at_utc
```

Saved SQL and quality checks select named business columns and are expected to
ignore additive publication evidence. Live Databricks compatibility and query
performance remain authenticated runtime boundaries.

## Failure And Recovery

Expected sequence:

```text
composite warehouse audit
  -> STARTED manifest
  -> replace same-run histories for six dimensions and two facts
  -> reconcile all persisted histories
  -> COMMITTED manifest last
  -> verify all eight current views
```

A pre-commit failure attempts a bounded `FAILED` transition and leaves the
preceding warehouse generation current. A post-commit view failure cannot demote
the committed histories; rerunning the same ID reconciles and repairs views.

Operator rollback changes a bad manifest away from `COMMITTED`, causing all
eight current views to fall back together. Physical corruption requires
restoring recorded Delta versions for histories before restoring the manifest
and view definitions.

## Storage, Retention And Cost

Storage grows by eight history slices per committed generation plus uncleaned
failed attempts. The pre-existing detailed warehouse audit remains, followed by
aggregate candidate and persisted-history fingerprint scans. This is more work
than blind overwrite, but it closes the mixed star-schema publication gap.

Automatic retention is deferred until reporting, audit, and recovery windows are
approved. The recovery runbook prohibits routine deletion of committed runs.

## Security And Evidence Boundary

Manifest evidence contains relation names, aggregate counts, canonical schemas,
and hashes; it does not expose client, machine, fault, cost, parts, sensor, or
assignment values. Failures report bounded control stages and aggregate
findings.

Repository CI proves warehouse audit ordering, all-eight visibility selection,
fingerprint reconciliation, and source-controlled view SQL. It does not prove
Delta `MERGE`/`DELETE`, table-to-view migration, effective grants, schema
evolution, query performance, or runtime rollback.

## Rollback

Source rollback is a revert of the eventual squash commit. Runtime rollback must
follow `docs/runbooks/warehouse_publication_recovery.md`; reverting source alone
does not convert deployed current views back into legacy physical tables.
