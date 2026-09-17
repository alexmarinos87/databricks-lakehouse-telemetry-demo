# Change Brief: Reject incomplete default fixture discovery

## Problem

The standalone profiler and combined snapshot select increments with Path.glob.
An existing regular file at data/increments silently yields no matches. In a real
unprivileged local Python 3.13 subprocess, an unreadable increments directory also
yielded no glob matches while direct scandir raised PermissionError. A sample-only
selection can therefore hide failed discovery. Repeating that same selection is
not proof of completeness. This does not show that earlier fixture totals were wrong.

Python documents suppressed scanning errors for glob, including PermissionError:
https://docs.python.org/3.13/library/pathlib.html#pathlib.Path.glob

## Acceptance Criteria

- Both default selectors use one bounded scanner, preserving sorted immediate CSV
  selection, platform casing rules and legitimate missing/empty optional increments.
- A missing mandatory data parent, non-directory or symlink directory, scan open or
  iteration failure, and observed directory replacement fail with stable categories.
- Scan at most 1,001 entries to enforce a 1,000-entry directory budget, including
  unrelated names. Select at most 99 increments plus the sample. The combined
  snapshot retains its tighter 32-file limit and filename allowlist.
- Preserve individual-file validation, source rechecks, exact costs, replay grain,
  successful output schema and explicit --source behavior. Hash the scanner source.
- Test real filesystem behavior and callers; observe current-head CI, the actual
  acceptance script, review generation and actual artifact publication.

## Scope And Non-Goals

One small standard-library scanner, the two selecting modules, focused tests and
this brief. No workflow, source fixture, dependency, source-validator, table/schema,
checkpoint, permission, deployment or runtime changes. This extends the existing
main-targeted draft #147, not the predecessor branches. No independent approval,
human acceptance, provider authentication or merge is implied.

## Architecture And Compatibility

The scanner receives a normalized caller-controlled repository root. It checks
both data and increments directory types, uses explicit scandir error handling,
counts entries before filtering, and compares directory metadata after scanning.
Missing increments remain optional; a present but inaccessible directory is not.
The profiler translates scanner errors into DatasetProfileError; the combined
builder retains PortfolioSnapshotError and its final fixture-set recheck. The
scanner does not parse CSV or replace descriptor-based file checks. Discovery is
not an atomic filesystem snapshot or a hostile-ancestor-rename security boundary.
Directories with over 1,000 entries are deliberately rejected rather than scanned
without a ceiling. Split/clean the fixture directory; do not silently skip entries.

## State, Security, Cost And Recovery

Discovery is read-only. Tests write disposable temporary directories only. No
credentials, network requests or source contents are copied into diagnostics.
Metadata checks and one bounded scan replace glob enumeration. In a full build,
existing final-selection and CI reconstruction checks may repeat the bounded scan.
No successful package is emitted on discovery failure. Correct permissions/layout
and regenerate into a fresh output directory; existing outputs remain untouched.
Removing this fix restores the known incomplete-discovery risk. No data, Delta,
checkpoint, ACL or schema recovery is involved; RTO/RPO and compute SKU are N/A.

## Validation And Review

Fourteen scanner tests execute the complete standard-library module locally:
optional absence, ordering, malformed/symlink parents, open/iteration failures,
matching/nonmatching budgets, early termination and post-scan replacement/removal.
Five integration tests exercise both selectors, actual CLI processes, sample-only
compatibility, committed fixture aggregates and selected-source provenance.
Full upstream integrations and acceptance require GitHub CI because this authoring
container cannot clone GitHub. Report local and remote evidence separately.
Inspect discovery failure propagation, the two independent limits, parent identity
rechecks and inclusion of the scanner in EVIDENCE_PATHS. Independent correctness
and adversarial engineering reviews and exact human acceptance remain pending.
