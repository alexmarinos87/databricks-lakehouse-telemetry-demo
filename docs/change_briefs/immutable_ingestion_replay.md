# Change Brief: Immutable ingestion identity and explicit replay/backfill

## Problem

The deployment workflow uploads synthetic data to a fixed landing filename with
`--overwrite`. Auto Loader reuses a persistent checkpoint and, by default, treats
files as exactly-once inputs. Replacing bytes behind a previously observed path
therefore creates an ambiguous source history: the changed file might not be
processed, and enabling overwrite processing could ingest an uncontrolled file
version or duplicate.

The setup guide also suggests clearing checkpoint and schema state to replay
files. That couples data repair to destructive stream-state changes and provides
no bounded replay identity.

## Acceptance Criteria

- Every governed upload destination contains the full local SHA-256 digest.
- Repeating the same incremental file resolves to the same destination and is a
  verified no-op when the remote bytes match.
- Different bytes resolve to a different immutable destination, even when the
  local basename is unchanged or renamed.
- Backfill requires a reviewed replay ID and creates a distinct path without
  deleting or replacing the normal checkpoint.
- The uploader never passes `--overwrite` and never invokes a remove command.
- Existing destinations are read back and verified byte-for-byte before being
  accepted as idempotent.
- Upload manifests bind local source path, size, digest, immutable object name,
  destination and the `reuse_existing_checkpoint` policy.
- The bronze notebook rejects unmanaged landing names before starting Auto
  Loader and records mode, replay ID, content digest and object name as lineage.
- `cloudFiles.allowOverwrites` is explicitly false.
- Repeated upload, changed content, late increment, backfill, local tampering,
  remote mismatch, race and checkpoint-policy scenarios have executable tests.
- There is **no implicit checkpoint deletion** in repository code or workflow
  configuration.

## Non-Goals

- This increment does not reset a live checkpoint, delete a landing object,
  replay production data, or run a Databricks workflow.
- It does not provide an object-store-side checksum API for large production
  files. The demo uploader bounds files to 10 MiB and verifies them with
  `databricks fs cat`.
- It does not make an external writer obey this naming contract. The bronze
  preflight fails closed when unmanaged files are present, but a file arriving
  after preflight remains a time-of-check/time-of-use residual risk.
- It does not bypass Silver event-ID replay and conflict controls. A backfilled
  object is re-read as a new file; downstream event identity still decides
  whether rows are identical replays or conflicts.
- It does not change Auto Loader checkpoint or schema-location paths.

## Object Identity

Incremental object:

```text
machine-events__incremental__sha256_<64 hex>.csv
```

Backfill object:

```text
machine-events__backfill__replay_<replay-id>__sha256_<64 hex>.csv
```

The full digest is retained in the name. The replay ID is bounded to 64 safe
characters and is mandatory only for backfill mode.

## Checkpoint And Replay Semantics

- Normal incremental delivery keeps the existing checkpoint and uploads a new
  content-addressed object only when the bytes are new.
- Repeating identical incremental bytes is an idempotent verified skip.
- Corrected bytes use a new digest and therefore a new source-object identity.
- Intentional replay of the same bytes uses `backfill` plus a new replay ID,
  resulting in a file path Auto Loader has not previously recorded.
- Checkpoint deletion is an incident-level recovery operation, not an upload or
  backfill mechanism. It requires separate approval, target-table analysis and
  a recorded rollback plan.

## Security And Failure Behaviour

- Source paths must be regular repository files; symlinks and paths outside the
  repository root are rejected.
- File count, per-file bytes, total bytes and manifest bytes are bounded.
- Manifest and source-file tampering fails before any Databricks command.
- CLI output and remote file content are captured, hashed and not echoed into
  broad logs.
- A remote object at the immutable path with different bytes is a hard failure.
- A concurrent-create race is accepted only after an exact remote reread.
- Errors identify the control stage or exit code without source rows, file
  contents or provider diagnostics.

## Migration And Rollback

Before first use, move any legacy fixed-name CSV objects out of the watched
landing root. Do not overwrite or rename a file behind an already processed
path and assume Auto Loader will replay it. Re-upload the preserved local source
through the planner and uploader so its immutable name is deterministic.

If a live bronze table contains rows without immutable identity columns, stop
and decide whether to preserve that table as a legacy snapshot or backfill its
lineage from independently verified source evidence. The notebook does not
invent digests for historical rows.

Source rollback is a revert of the eventual squash commit. Runtime rollback does
not delete the checkpoint: remove the newly uploaded immutable object only
through an independently approved recovery procedure, and assess whether its
rows already reached Bronze and downstream tables before any deletion.

## Evidence Boundary

Repository tests prove deterministic naming, manifest validation, uploader
command construction, byte verification and Spark lineage extraction. They do
not prove effective Databricks Files API behaviour, Unity Catalog volume
permissions, Auto Loader discovery, or a live backfill. Those remain
Databricks-runtime evidence and require explicit authorization.
