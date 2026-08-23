# Change Brief: Version the five Gold outputs behind one committed manifest

## Problem

The Gold notebook currently overwrites five independently visible Delta tables.
A task interruption can leave uptime, failure, maintenance-cost, parts-usage, and
client-summary relations from different workflow generations. Warehouse,
forecast, quality, and SQL consumers can therefore read a structurally valid but
mixed Gold state.

## Outcome

Publish all five Gold outputs as one versioned family:

```text
gold_machine_uptime_history
gold_failure_events_history
gold_maintenance_costs_history
gold_parts_usage_history
gold_client_asset_summary_history
gold_publication_manifest
```

The existing consumer names become current views that select only the latest
`COMMITTED` `gold_publication_run_id`.

## Acceptance Criteria

- The workflow supplies `gold_publication_run_id = job_{{job.run_id}}`.
- Every candidate and history row carries the same publication run identity.
- The exact set of five governed Gold outputs is required; missing or unexpected
  transformation outputs fail before publication.
- Candidate evidence records row counts, canonical schemas, and bounded
  order-independent payload fingerprints for every dataset.
- One `STARTED` manifest precedes run-scoped history replacement.
- All five persisted history slices reconcile before `COMMITTED`.
- `COMMITTED` is the final visibility write.
- `STARTED`, `FAILED`, and absent-manifest generations remain hidden from every
  current Gold view.
- A committed retry is read-only and verifies all five histories before
  repairing views.
- Existing views use `ALTER VIEW ... AS`; absent views use `CREATE VIEW`.
- Legacy physical current tables fail before any publication mutation.
- Analyst grants target the five current views, not backing histories or the
  manifest.
- Spark tests prove all-five switching, partial-run isolation, same-count
  corruption, empty optional output, and missing-history detection.

## Non-Goals

- This is manifest-last visibility, **not cross-table ACID** across five history
  tables and one manifest.
- It does not version warehouse outputs; that is the next G10 layer.
- It does not deploy, migrate live tables, execute Databricks SQL, or alter
  permissions.
- It does not expose Gold histories or the manifest directly to analysts.
- It does not define automatic history retention or `VACUUM` timing.

## Compatibility And Migration

The five current names change from physical tables to views. The notebook
refuses implicit replacement. Before first runtime use, an operator must preserve
and rename all legacy physical Gold tables, record Delta versions, schemas,
counts, ownership, grants, and business-date ranges, then run one governed Gold
generation.

Each current view adds:

```text
gold_publication_run_id
publication_completed_at_utc
```

Warehouse, forecast, quality, and reporting transformations select named
business columns and are expected to ignore additive publication evidence.
Live Databricks compatibility remains an authenticated runtime boundary.

## Failure And Recovery

Expected order:

```text
STARTED manifest
  -> replace same-run history for each Gold dataset
  -> reconcile all persisted histories
  -> COMMITTED manifest last
  -> verify all five current views
```

A pre-commit failure attempts a bounded `FAILED` transition and leaves the
previous committed generation current. A failure after commit does not demote
the generation; rerunning the same ID reconciles histories and repairs views.

Operator rollback changes an accepted bad manifest away from `COMMITTED`, which
causes every current Gold view to fall back together. Corrupt physical data is
restored from recorded Delta versions before restoring the manifest and view
definitions.

## Storage, Retention And Cost

Storage grows by five history slices per committed generation plus uncleaned
failed attempts. Every run performs aggregate candidate and persisted-history
evidence scans. This is deliberately more expensive than five blind overwrites
because it prevents mixed generations from becoming current.

Automatic retention is deferred until reporting, forecast, investigation, and
recovery windows are approved. The runbook prohibits routine deletion of a
committed run.

## Security And Evidence Boundary

Manifest evidence contains dataset names, aggregate counts, schemas, and hashes;
it does not contain machine, client, fault, cost, or parts values. Failure
messages use bounded control-stage identifiers and aggregate findings.

Repository CI proves DataFrame selection, multi-dataset reconciliation, view SQL
construction, and write ordering. It does not prove live Delta `MERGE`/`DELETE`,
table-to-view migration, effective Unity Catalog grants, schema evolution, or
runtime rollback.

## Rollback

Source rollback is a revert of the eventual squash commit. Runtime rollback must
follow `docs/runbooks/gold_publication_recovery.md`; reverting source alone does
not convert deployed current views back into legacy tables.
