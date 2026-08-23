# Silver Publication Recovery

## Object model

Durable Delta tables:

```text
silver_machine_events_history
silver_quarantine_machine_events_history
silver_publication_manifest
```

Current consumer views:

```text
silver_machine_events
silver_quarantine_machine_events
```

The views rank only `COMMITTED` manifest rows and expose one latest
`silver_publication_run_id`. `STARTED`, `FAILED`, and absent-manifest history is
not current.

## First adoption from legacy physical tables

The notebook blocks when either current name is a physical table. Do not add a
scheduled `DROP TABLE` or implicit replacement.

1. Pause the workflow.
2. Record each legacy table's Delta version, schema, row count, owner, grants,
   minimum and maximum event timestamp, and source-file count.
3. Preserve each physical table under a dated legacy name or reviewed shallow
   clone.
4. Confirm the original current names no longer resolve to physical tables.
5. Run one new Silver publication with a new workflow run ID.
6. Verify one `COMMITTED` manifest row, both current view run IDs, row counts,
   current-view schemas, Gold compatibility, quality checks, ownership, and
   effective grants.
7. Retain the legacy snapshots until the approved recovery window expires.

Do not invent a run identity or content fingerprint for legacy rows without
independent source evidence.

## Publication states

- `STARTED`: one run may be replacing its own uncommitted histories.
- `COMMITTED`: both histories match the recorded evidence and no conflicting
  event-ID payloads blocked trust.
- `FAILED`: publication stopped before commit. The bounded `failure_code`
  identifies the control stage.

## Conflict investigation

A conflict run is expected to be `FAILED`. Its rows remain in
`silver_quarantine_machine_events_history` under the exact
`silver_publication_run_id`, while the current quarantine view remains on the
previous committed generation.

Inspect bounded counts and conflict reasons for that run. Do not copy rejected
payload values into broadly visible issue or CI logs. Resolve source ownership
and event identity before using the same run ID for a repair or a new run ID for
a corrected delivery.

## Retry and repair

A retry may reuse the same run ID only when its state is `STARTED`, `FAILED`, or
absent. The notebook deletes and replaces only that run's quarantine and Silver
history slices.

When the run is already `COMMITTED`, the notebook performs a read-only evidence
reconciliation and repairs the current view definitions. It must not rewrite a
committed history generation automatically.

## Partial publication

The expected write order is:

```text
STARTED manifest
  -> quarantine history
  -> trusted Silver history
  -> persisted evidence reconciliation
  -> conflict gate
  -> COMMITTED manifest
  -> current-view verification
```

A failure before `COMMITTED` leaves the previous committed run visible. This is
manifest-last visibility, not cross-table ACID.

## Find orphan or incomplete generations

Identify distinct run IDs in both histories and compare them with the manifest.
Review:

- history with no manifest row;
- `STARTED` runs older than the accepted execution window;
- `FAILED` runs with retained quarantine history;
- manifest counts or hashes that do not match stored histories.

Record the workflow run, row counts, failure code, current Delta versions, and
investigation owner before cleanup.

## Cleanup

For an accepted non-committed orphan:

1. Record the current Delta version for quarantine history, Silver history, and
   manifest.
2. Delete that run from quarantine history.
3. Delete that run from Silver history.
4. Delete its `STARTED` or `FAILED` manifest row.
5. Re-run orphan detection and current-view reconciliation.

**Do not delete a committed run** as routine cleanup. Committed retention needs
an approved recovery window and consumer-impact review.

## Roll back a bad committed generation

1. Confirm a preceding `COMMITTED` run exists and reconciles.
2. Record the bad and fallback run IDs plus all three Delta versions.
3. Change the bad manifest from `COMMITTED` to `FAILED` with a bounded operator
   rollback code.
4. Verify both current views fall back to the preceding run.
5. Verify Gold and quality reads before resuming the workflow.
6. Retain bad histories for investigation until deletion is separately approved.

If physical data is corrupt, restore Delta versions in this order:

1. `silver_quarantine_machine_events_history`;
2. `silver_machine_events_history`;
3. `silver_publication_manifest`;
4. reapply or alter both current views and verify their selected run.

## Evidence to retain

- Databricks workflow and repair-run identifiers;
- `silver_publication_run_id`;
- before and after Delta versions;
- manifest state transition and failure code;
- aggregate Silver, quarantine, replay, and conflict counts;
- selected current-view run ID;
- operator and reviewer;
- whether any history was deleted.

Repository tests are not Databricks runtime recovery evidence.
