# Change Brief: Make default fixture suffix selection deterministic

## Problem

On `a85d66f`, default discovery uses `fnmatch.fnmatch`, which applies the operating
system's case normalization. Real files `a.csv`, `b.CSV`, `c.CsV` and
`UPPER_STEM.csv` select two paths under POSIX matching but four when fnmatch uses
standard-library Windows normcase. The same supplied names can therefore produce
different source evidence and matching-file budget results.

Python documents this distinction in the
[fnmatch reference](https://docs.python.org/3.11/library/fnmatch.html):
`fnmatchcase` applies case-sensitive matching without OS normalization.

## Acceptance Criteria

- Default discovery applies case-sensitive `*.csv` matching on every platform.
  Keep original filename case and deterministic relative-path ordering.
- Lowercase suffixes, uppercase stems and dot-prefixed names keep working;
  uppercase/mixed suffixes and nested files are not implicitly selected.
- Excluded names do not consume the matching-file budget but still consume the
  directory-entry budget. Preserve both existing ceilings.
- Reproduce old platform-dependent selection and verify corrected discovery
  against real directory entries and exact reader bytes/hashes.

## Architecture Boundaries And Compatibility

Only `src/lakehouse_demo/fixture_discovery.py`,
`tests/test_fixture_discovery_case.py` and this brief change. One matcher call and
one docstring sentence change; scan mechanics and public signatures do not.

Linux/CI default discovery is preserved. Windows callers that previously relied
on `.CSV` or `.CsV` being implicitly selected must use canonical lowercase
suffixes or the existing explicit-source input where supported. This change does
not rename files or modify explicit-source selection. No case folding of output
paths, filesystem case-alias detection, recursive scanning or new dependencies.
Optional absence, file-type validation delegated to readers, error categories,
metadata race checks and caller-controlled normalized-root assumptions remain.

## Data, State, Permissions And Cost

No committed data, business transformation, grain, schema, keys, null policy,
monetary/timestamp policy, deduplication, checkpoint, replay or backfill change.
This is local source selection, not an Auto Loader configuration change. No
provider, credentials, permissions, workspace or production state operations.
Existing scan/file ceilings, job limits and schedules remain unchanged. Tests use
small owned temporary directories; no compute SKU or retained storage increase.

## Failure And Recovery

Scan and reader failures keep their existing categories. Revert this commit to
restore the OS-dependent matcher. Rebuild source evidence when changing fixture
selection; never relabel prior packages. No data/Delta/checkpoint/ACL restore or
RTO/RPO applies. No native Windows filesystem execution is claimed by this work.

## Validation And Review

Five methods run actual directory discovery with explicit POSIX and Windows
normalization semantics. A positive probe verifies the emulation really exposes
the original fnmatch difference. A real-reader integration verifies retained path
case and payload hashes. This is not native Windows runner or case-alias evidence.
Local source-subset tests cannot replace full repository acceptance. Observe
existing CI acceptance/review generation, Spark and source-artifact verification
on the combined head. Independent correctness/adversarial reviews and explicit
human acceptance remain pending; author regression probes are not those reviews.
