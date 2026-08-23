# Gold Publication Recovery

## Object model

Durable Delta tables:

```text
gold_machine_uptime_history
gold_failure_events_history
gold_maintenance_costs_history
gold_parts_usage_history
gold_client_asset_summary_history
gold_publication_manifest
```

The five current views retain the original consumer names:

```text
gold_machine_uptime
gold_failure_events
gold_maintenance_costs
gold_parts_usage
gold_client_asset_summary
```

All five current views select one latest `COMMITTED`
`gold_publication_run_id`. `STARTED`, `FAILED`, and absent-manifest history is
not current.

## First adoption from legacy physical tables

The notebook blocks when any current Gold name is a physical table. Do not add a
scheduled drop or implicit replacement.

1. Pause the workflow and dependent reporting refreshes.
2. Record the Delta version, schema, row count, owner, grants, minimum and
   maximum business date, and accepted control totals for every legacy Gold
   table.
3. Preserve each physical table under a dated legacy name or reviewed clone.
4. Confirm none of the five original names resolves to a physical table.
5. Run one new Gold generation using a new workflow run ID.
6. Verify one `COMMITTED` manifest row and that all five current views carry the
   same run ID and recorded row counts.
7. Run warehouse reconciliation, forecast validation, quality checks, saved SQL
   queries, ownership checks, and effective analyst grants.
8. Retain the legacy snapshots until the approved recovery window expires.

Do not invent manifest evidence for legacy rows without independently verified
source and transformation evidence.

## Publication states

- `STARTED`: one run may be replacing only its own history slices.
- `COMMITTED`: all five persisted histories match recorded evidence.
- `FAILED`: publication stopped before commit; the bounded `failure_code`
  identifies the control stage.

## Retry and repair

A retry may reuse the same run ID only when its state is `STARTED`, `FAILED`, or
absent. The notebook deletes and replaces only that run from each Gold history.

When the run is already `COMMITTED`, the notebook performs read-only evidence
reconciliation across all five histories and repairs current-view definitions.
It must not rewrite committed history automatically.

## Partial publication

Expected sequence:

```text
STARTED manifest
  -> five run-scoped history replacements
  -> all-history evidence reconciliation
  -> COMMITTED manifest
  -> five current-view verifications
```

A failure before `COMMITTED` leaves the preceding generation visible through all
five current views. This is manifest-last visibility, not cross-table ACID.

## Identify incomplete or orphan generations

Compare distinct `gold_publication_run_id` values in all histories with the
manifest. Review:

- history with no manifest;
- `STARTED` runs older than the accepted workflow window;
- `FAILED` runs and their failure codes;
- missing dataset histories;
- row-count, schema, or payload-fingerprint mismatches.

Retain workflow identifiers, aggregate counts, Delta versions, and an
investigation owner before cleanup.

## Cleanup

For one accepted non-committed orphan:

1. Record current Delta versions for all five histories and the manifest.
2. Delete that run from each history.
3. Delete its `STARTED` or `FAILED` manifest row.
4. Re-run orphan detection and all-five current-view reconciliation.
5. Confirm warehouse, forecast, and quality consumers remain on the same
   committed Gold run.

**Do not delete a committed run** as routine cleanup. Committed retention needs
an approved reporting and recovery window.

## Roll back a bad committed generation

1. Confirm a preceding `COMMITTED` generation exists and all five histories
   reconcile.
2. Record the bad and fallback run IDs plus every relevant Delta version.
3. Change the bad manifest away from `COMMITTED` using a bounded operator
   rollback code.
4. Verify all five current views fall back to the same preceding run.
5. Re-run warehouse, forecast, quality, saved-query, ownership, and grant checks.
6. Retain the bad histories for investigation until deletion is separately
   approved.

If physical data is corrupt, restore Delta versions for all five histories,
then restore `gold_publication_manifest`, then reapply or alter all five current
views and verify their selected run.

## Evidence to retain

- Databricks workflow and repair-run identifiers;
- `gold_publication_run_id`;
- before and after Delta versions for every history and manifest;
- manifest state transition and failure code;
- dataset row counts, schemas, and bounded fingerprints;
- selected current-view run IDs;
- downstream warehouse, forecast, and quality results;
- operator and reviewer;
- whether any history was deleted.

Repository tests are not Databricks runtime recovery evidence.
