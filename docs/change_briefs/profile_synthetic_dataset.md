# Change Brief: Profile the committed synthetic dataset deterministically

## Problem

The repository validates its machine-event fixtures but does not provide a compact,
repeatable answer to basic portfolio questions such as row coverage, unique event
identity, machine/client/site breadth, operational-state distribution, represented
faults, downtime, and maintenance cost. Readers currently have to inspect raw CSV
or infer those facts from downstream transformations.

## Acceptance Criteria

- [ ] A standard-library API profiles one or more repository-contained machine-event
      CSV fixtures only after the existing source contract accepts them.
- [ ] The profile records deterministic file digests, physical and unique event
      counts, identical replay counts, coverage, operational counts, and bounded
      aggregate measures without retaining raw rows. Operational aggregates use the
      first validated row for each unique `event_id`, so identical replays do not
      inflate portfolio measures.
- [ ] Conflicting duplicates, invalid source contracts, symlinks, non-regular files,
      files outside the repository, oversized input, input replacement, and output
      overwrite attempts fail closed with sanitized categories.
- [ ] A CLI writes one new JSON and Markdown package and reports a bounded summary.
- [ ] Unit tests cover accepted data, replay identity, invalid data, path boundaries,
      deterministic output, and non-overwrite behavior.

## Non-Goals

- Running Spark, Auto Loader, Delta Lake, Unity Catalog, Lakeflow, or Databricks.
- Replacing the existing machine-event validation contract.
- Persisting row-level data, publishing a dashboard, or claiming runtime evidence.
- Profiling arbitrary external or client data.

## Architecture Boundaries

- Reuse the bounded `repository_files.py` helper from predecessor PR #137.
- New reusable module: `src/lakehouse_demo/dataset_profile.py`.
- New CLI: `scripts/profile_synthetic_dataset.py`.
- The existing `MACHINE_EVENT_COLUMNS` and `validate_machine_event_files` contracts
  remain authoritative and unchanged.
- Inputs are bounded repository files; outputs are a newly created local directory.

## Data, State And Side Effects

- Inputs: committed sample and increment CSVs, or explicit repository-relative CSVs.
- Output grain: one aggregate profile package for the complete selected file set.
- Event identity: `event_id`; physical rows are counted separately, while coverage
  and operational aggregates use the first validated observation per unique ID.
  Identical repeats are counted as replay evidence and conflicting repeats block
  through the existing validator.
- Input files are read-only and checked before and after contract validation.
- Output is non-overwriting. A failed write removes its incomplete directory.
- No table, checkpoint, source object, workflow, or external state is changed.

## Security, Permissions And Cost

- No credentials, network calls, subprocesses, provider APIs, or elevated identity.
- Raw rows and field values are not copied to the package; only aggregates, relative
  paths, sizes, and SHA-256 digests are retained.
- Cost is local standard-library parsing of at most 100 files, 2 MB per file, and
  10 MB total input. External-service and compute-SKU cost: N/A.

## Failure And Recovery

- Invalid paths, source contracts, changed files, limits, or existing output block.
- Failures are raised as bounded categories rather than raw record diagnostics.
- Recovery is to correct the source selection or remove/select a new output path and
  rerun. No data restoration, Delta version, checkpoint, permission, or ACL recovery
  is applicable.
- RTO/RPO: N/A; this is reproducible source evidence.

## Validation Plan

- Standard-library unit tests for valid multi-file profiling and aggregate semantics.
- Adversarial tests for identical/conflicting duplicates, invalid headers, symlinks,
  outside-root paths, and output overwrite.
- Repository-wide CI and generated pull-request review package.
- Spark and Databricks runtime checks are not required because no Spark source,
  notebook, dependency, bundle, table, checkpoint, or provider interaction changes.
