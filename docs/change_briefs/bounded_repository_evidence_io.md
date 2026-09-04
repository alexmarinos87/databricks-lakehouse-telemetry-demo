# Change Brief: Bound repository evidence file I/O

## Problem

Several repository evidence tools need the same safety properties when reading
source-controlled files and writing review packages: repository containment,
regular-file and symbolic-link checks, bounded bytes, change detection, sanitized
failures, and non-overwriting output. Reimplementing those boundaries in each new
tool would invite drift and make review harder.

## Acceptance Criteria

- [ ] One standard-library helper reads unique repository-contained regular files
      in deterministic relative-path order.
- [ ] Reads enforce configurable file-count, per-file byte, and total-byte limits.
- [ ] Symbolic links, non-regular files, outside-root paths, unavailable files,
      replacement during a read, and provider failures fail closed with sanitized
      categories.
- [ ] A caller can verify that a previously captured file set has not been replaced
      or changed.
- [ ] Text packages are written only to a new directory, reject unsafe filenames,
      never overwrite existing state, and remove partial output after failure.
- [ ] Focused tests exercise success, limits, path attacks, changed input, sanitized
      failures, non-overwrite behavior, and failed-write cleanup.

## Non-Goals

- Parsing any business-data schema or defining a portfolio report.
- Reading Git history, network resources, GitHub settings, or Databricks state.
- Replacing the repository contract scanner or protected runtime-evidence storage.
- Supporting in-place output updates or destructive cleanup.

## Architecture Boundaries

- New reusable module: `src/lakehouse_demo/repository_files.py`.
- New focused tests: `tests/test_repository_files.py`.
- The helper accepts files only; directory traversal or recursive discovery remains
  the responsibility of a bounded caller.
- Existing source, table, workflow, and evidence schemas remain unchanged.

## Data, State And Side Effects

- Input grain: one immutable snapshot per selected regular file, containing relative
  path, bytes, SHA-256 digest, size, and filesystem identity.
- Inputs are read-only. Reads use no-follow and descriptor identity checks where the
  platform exposes them.
- Output grain: one newly created directory containing caller-supplied text files.
- Existing output is never replaced. A failed package write removes only the newly
  created incomplete directory.
- No table, object store, checkpoint, branch setting, or external state is changed.

## Security, Permissions And Cost

- No credentials, network calls, subprocesses, provider APIs, or elevated identity.
- Public failures expose bounded path labels and stable categories, not provider
  diagnostics or file contents.
- Default ceiling: 100 files, 2 MB per file, and 10 MB total input.
- Cost is local standard-library file I/O; service and compute-SKU cost: N/A.

## Failure And Recovery

- Expected failures are invalid roots, paths, entry types, limits, races, read errors,
  unsafe output names, existing output, and filesystem write errors.
- Recovery is to correct the input/output selection and rerun. There is no partial
  accepted package and no existing state to restore.
- Delta versions, checkpoints, data recovery, permissions, query ACLs, RTO and RPO:
  N/A.

## Validation Plan

- Standard-library tests for deterministic reads, limits, path containment, symlink
  and entry-type rejection, file replacement, sanitized read failure, non-overwrite,
  and output cleanup.
- Repository-wide CI and generated pull-request review package.
- Spark and Databricks runtime checks are not required because this helper changes no
  Spark source, notebook, dependency, bundle, table, checkpoint, or provider state.
