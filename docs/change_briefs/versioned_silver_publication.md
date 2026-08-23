# Change Brief: Version Silver publication behind a committed manifest

## Problem

The Silver notebook currently writes quarantine with a full overwrite and then
writes trusted Silver with another full overwrite. A task interruption can leave
the two relations from different generations. A conflicting event-ID run also
replaces the current quarantine table while intentionally leaving trusted Silver
unchanged, so the public pair no longer describes one run.

## Outcome

Publish trusted Silver and quarantine as one versioned family:

```text
silver_machine_events_history
silver_quarantine_machine_events_history
silver_publication_manifest
```

The existing consumer names become current views:

```text
silver_machine_events
silver_quarantine_machine_events
```

Both current views select only the latest `COMMITTED` Silver run. `STARTED`,
`FAILED`, and missing-manifest histories remain non-current.

## Acceptance Criteria

- The workflow supplies `silver_publication_run_id = job_{{job.run_id}}`.
- Both histories include the same run identity.
- Candidate and persisted histories are reconciled using row count, canonical
  schema, and bounded order-independent payload fingerprints.
- Quarantine history is written before trusted Silver history.
- A `COMMITTED` manifest is written only after both persisted histories match
  the exact candidate evidence and the run has no event-ID payload conflicts.
- A conflict run transitions to `FAILED`; its quarantine history remains
  available by run ID while both current views remain on the preceding commit.
- Retrying a non-committed run replaces only that run's history slices.
- Retrying a committed run is read-only and verifies persisted evidence.
- Existing views use `ALTER VIEW ... AS`; absent views use `CREATE VIEW`.
- A legacy physical object occupying a current view name fails before any
  publication mutation.
- Local Spark tests prove latest-commit selection, partial-run isolation,
  zero-row quarantine, same-count corruption, duplicate manifests, and terminal
  state validation.

## Non-Goals

- This is a manifest-last visibility protocol, **not cross-table ACID** across
  the two histories and manifest.
- It does not version Gold or warehouse outputs; those remain separate G10
  increments.
- It does not deploy, migrate live Delta tables, execute a workflow, alter
  permissions, or delete a failed generation.
- It does not expose failed quarantine history through the current quarantine
  view. Operators inspect history by the recorded run ID.
- It does not define automatic retention or `VACUUM` policy.

## Compatibility And Migration

The two current names change from physical tables to views. The notebook refuses
an implicit table-to-view replacement. Before first runtime use, an operator
must preserve and rename the legacy physical Silver and quarantine tables,
record Delta versions, row counts, schemas, ownership and grants, then run one
new governed generation.

Current views add two publication-evidence fields:

```text
silver_publication_run_id
publication_completed_at_utc
```

Gold and quality transformations are expected to ignore additive columns, but
that compatibility remains an authenticated Databricks runtime boundary.

## Failure And Recovery

Expected sequence:

```text
STARTED manifest
  -> replace same-run quarantine history
  -> replace same-run Silver history
  -> reconcile persisted histories
  -> reject event-ID conflicts
  -> COMMITTED manifest last
  -> verify both current views
```

A pre-commit failure attempts one bounded `FAILED` transition. The previous
committed generation remains current. A post-commit view verification failure
does not demote the committed run; rerunning the same ID reconciles history and
repairs view definitions.

Operator rollback changes an accepted bad manifest away from `COMMITTED`, which
causes current views to fall back to the preceding valid generation. Corrupt
physical data is restored using recorded Delta versions in quarantine-history,
Silver-history, then manifest order.

## Storage, Retention And Cost

History storage grows by every committed generation plus uncleaned failed or
started attempts. Each run computes bounded aggregate evidence over candidate
and persisted slices, adding Spark work compared with direct overwrite.

Automatic retention is deferred until recovery windows and investigation needs
are approved. The recovery runbook prohibits routine deletion of committed runs.

## Security And Evidence Boundary

Manifest evidence contains dataset names, aggregate counts, schemas, and hashes;
it does not contain rejected business values. Failure messages use bounded
control stages and aggregate findings.

Repository CI proves DataFrame selection, fingerprint reconciliation, source
ordering, and SQL construction. It does not prove Databricks Delta `MERGE` or
`DELETE`, table-to-view migration, effective Unity Catalog ownership, schema
evolution, or runtime rollback.

## Rollback

Source rollback is a revert of the eventual squash commit. Runtime rollback
requires the explicit procedure in
`docs/runbooks/silver_publication_recovery.md`; reverting source alone does not
convert deployed current views back into legacy physical tables.
