# Change Brief: Preserve evidence-output ownership on failure

## Problem

The draft repository-file helper wraps directory creation and package writing in
one cleanup block. A second writer can create the destination after the initial
existence check; the losing writer then receives FileExistsError and recursively
deletes the winner's package. Cleanup also lacks an identity check if the output
path is replaced during a write. Parent validation only examines the nearest
existing ancestor and misses a symlink higher in the supplied path.

## Acceptance Criteria

- A writer that fails to create the destination never recursively removes it.
- A concurrent creation conflict reports output_directory_exists and preserves
  the winner's bytes.
- Failed writes clean up only while the created directory's device/inode identity
  still matches; missing, replaced or uninspectable output is left untouched.
- All supplied output ancestors are checked for symlinks and non-directories,
  including when intermediate directories already exist or need creation.
- Ordinary nested output, sanitized errors and cleanup after partial/encoding
  failures retain their existing behaviour.
- Behavioural regression tests fail on the original helper and pass on the repair.
- Exact-head repository CI and artifact compatibility pass before acceptance.

## Non-Goals

- No new portfolio snapshot or CI-publication feature in this corrective change.
- No business-data, dataset-profile, ingestion, table, checkpoint, bundle,
  dependency, workflow, grant or Databricks behaviour changes.
- No GitHub settings mutation, workspace access, deployment or production action.
- This is not a security sandbox for hostile actors with write/rename access to
  the output parent. Parents must be caller-controlled for the whole operation.
- No atomic package-publication or cross-filesystem transaction guarantee.

## Architecture Boundaries

The public helper API is unchanged. Directory creation has its own failure
boundary; successful creation records a directory identity for best-effort
failure cleanup. Ancestor validation walks the complete supplied parent chain.
The new tests use temporary files and deterministic injected interleavings, not
sleep-based scheduling or source-text assertions.

This is a corrective draft stacked on #138, which depends on #137. It must be
included in the review of that stack; do not accept the original helper's claimed
non-overwrite/cleanup guarantee without this repair. It does not accept or merge
either predecessor and does not alter their branches.

## Data, State And Side Effects

Only a caller-selected new local evidence directory is written. Input files stay
read-only. A failed mkdir conveys no ownership. Cleanup is conservative when the
created directory cannot be identified. Newly created parent directories or
unverifiable/displaced partial output may remain for explicit operator inspection;
they are not successful evidence packages. No raw rows, credentials or provider
diagnostics are added to evidence or error messages.

## Failure, Recovery And Rollback

Correct the output selection and rerun into a fresh caller-controlled directory.
Do not recursively remove an uncertain path as part of automatic recovery. If a
partial package remains, inspect its ownership before removing it manually.
Rollback is to revert this commit on the draft branch; that restores the known
race and must not be presented as a safe production fallback. No data migration,
Delta version restore, checkpoint reset or permission rollback is involved.

## Validation And Review

The eight focused cases cover competing creation, failed creation without
cleanup, owned partial-write cleanup, path replacement, existing and missing
ancestors above a symlink, nested success and encoding failure. Five fail against
the original helper; all eight pass against the repair in local Python 3.13.
The local helper copy was verified against GitHub blob
4b92a313a71a9ad7c3d9d8c96aea21dcf112eca3 before reproduction.

The full repository acceptance script could not be run locally because this
session cannot clone GitHub from the execution container. Full-suite and generated
review-package evidence must come from exact-head GitHub CI, and its result must
be recorded separately. Spark and Databricks checks are not applicable to the
changed code and are not claimed as executed. Independent correctness and
adversarial reviews, followed by exact human acceptance, remain pending; author
self-review and tests do not replace them.

## Human Inspection Points

- write_new_text_package: creation failure must never enter cleanup.
- _remove_owned_output: unknown or changed identity must never trigger deletion.
- _verify_output_parent: all ancestors, including symlinks above existing parents.
- tests/test_repository_package_failures.py: winning and replaced bytes survive.
