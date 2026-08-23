# Warehouse Publication Recovery

## Object model

Durable Delta tables:

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

The eight current views retain the original dimension and fact names. They all
select one latest `COMMITTED` `warehouse_publication_run_id`. `STARTED`,
`FAILED`, and absent-manifest histories are not current.

## First adoption from legacy physical tables

The notebook blocks when any current warehouse name is a physical table. Do not
add a scheduled drop or implicit replacement.

1. Pause the workflow and reporting refreshes.
2. Record every legacy relation's Delta version, schema, row count, owner, and
   grants.
3. Record fact control totals, grain counts, null keys, unmatched dimensions,
   assignment-version counts, and current warehouse audit findings.
4. Preserve each physical dimension and fact under a dated legacy name or
   reviewed clone.
5. Confirm none of the eight original names resolves to a physical table.
6. Run one new warehouse generation using a new workflow run ID.
7. Verify one `COMMITTED` manifest and that all eight current views carry the
   same run ID and manifest row counts.
8. Re-run the composite warehouse audit, quality checks, saved queries,
   ownership checks, grants, and representative query-performance evidence.
9. Retain legacy snapshots until the approved recovery window expires.

Do not manufacture manifest evidence for legacy rows without independently
verified Gold and transformation evidence.

## Publication states

- `STARTED`: one run may replace only its own eight history slices.
- `COMMITTED`: all six dimensions and both facts match recorded evidence.
- `FAILED`: publication stopped before commit; a bounded `failure_code`
  identifies the control stage.

## Retry and repair

A retry may reuse the same run ID only when its state is `STARTED`, `FAILED`, or
absent. The notebook deletes and replaces only that run from every warehouse
history.

When already `COMMITTED`, the notebook performs read-only evidence
reconciliation across all eight histories and repairs current-view definitions.
It must not rewrite committed history automatically.

## Partial publication

Expected sequence:

```text
composite warehouse audit
  -> STARTED manifest
  -> six dimension and two fact history replacements
  -> all-history evidence reconciliation
  -> COMMITTED manifest
  -> eight current-view verifications
```

A failure before `COMMITTED` leaves the preceding complete warehouse current.
This is manifest-last visibility, not cross-table ACID.

## Identify incomplete or orphan generations

Compare distinct `warehouse_publication_run_id` values in every history with the
manifest. Review:

- history with no manifest;
- `STARTED` runs older than the accepted workflow window;
- `FAILED` runs and failure codes;
- missing dimension or fact histories;
- row-count, schema, or payload-fingerprint mismatches;
- any mismatch between current-view run IDs.

Record workflow identifiers, all Delta versions, aggregate findings, and an
investigation owner before cleanup.

## Cleanup

For one accepted non-committed orphan:

1. Record current Delta versions for all eight histories and the manifest.
2. Delete that run from every history.
3. Delete its `STARTED` or `FAILED` manifest row.
4. Re-run orphan detection and all-eight current-view reconciliation.
5. Re-run warehouse, quality, and representative saved-query checks.

**Do not delete a committed run** as routine cleanup. Committed retention needs
an approved audit, reporting, and recovery window.

## Roll back a bad committed warehouse

1. Confirm a preceding `COMMITTED` generation exists and all eight histories
   reconcile.
2. Record bad and fallback run IDs plus every history and manifest Delta version.
3. Change the bad manifest away from `COMMITTED` using a bounded operator
   rollback code.
4. Verify all eight current views fall back to the same preceding run.
5. Re-run the composite warehouse audit, quality checks, saved queries,
   ownership, grants, and representative performance checks.
6. Retain the bad histories for investigation until deletion is separately
   approved.

If physical data is corrupt, restore Delta versions for all eight histories,
then restore `warehouse_publication_manifest`, then reapply or alter all eight
current views and verify their selected run.

## Evidence to retain

- Databricks workflow and repair-run identifiers;
- `warehouse_publication_run_id`;
- before and after Delta versions for every history and manifest;
- manifest state transition and failure code;
- row counts, schemas, and bounded fingerprints;
- composite warehouse-audit result;
- selected run ID for all eight current views;
- query and quality evidence;
- operator and reviewer;
- whether any history was deleted.

Repository tests are not Databricks runtime recovery evidence.
