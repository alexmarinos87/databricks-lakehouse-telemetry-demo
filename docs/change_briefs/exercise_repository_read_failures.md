# Change Brief: Exercise repository descriptor failure paths

## Problem And Scope

The existing reusable-file tests cover ordinary reads, configured limits,
post-capture changes and an open failure. Important opened-descriptor paths need
more regression evidence. This is a coverage increment, not a reproduced defect
or a correction to production file handling.

Only `tests/test_repository_read_failures.py` and this brief change. The helper
`src/lakehouse_demo/repository_files.py` remains byte-for-byte unchanged. Nine
standard-library methods use real temporary files and descriptors, with narrow
OS-call injection for repeatable failures and controlled mutation timing.

## Acceptance Criteria

- Multi-chunk reads retain exact bytes, size and SHA-256 with bounded requests.
- Read errors and either descriptor-stat error yield a sanitized public category
  and release the opened descriptor.
- Mid-read growth, truncation and same-size rewrites fail; early EOF cannot be
  accepted solely because metadata stayed unchanged.
- Replacement between inspection and open and an opened pipe cannot pass as the
  expected regular file. The pipe is rejected before any read, avoiding blocking.
- Growth before open rechecks both per-file and remaining-total byte limits before
  reading. Successful reads also close the descriptor.
- Focused tests pass on unchanged source; isolated guard-removal controls should
  demonstrate that representative protections are actually exercised.

## Architecture, State And Non-Goals

Exercise the public `read_repository_files` function rather than extracting or
rewriting its implementation. Inject only individual OS-call seams; validate real
file contents and descriptors with unpatched OS functions. Cleanup handlers keep
intentional failing mutation probes from leaking their own descriptors.

No product, schema, fixture, numeric policy, timestamp behavior, checkpoint,
replay, permission, interface, package format or workflow change in this increment.
No network, credentials, external provider, Databricks, Delta, catalog or production
state. Parent directories remain caller-controlled. This is not comprehensive
race testing, hostile-concurrent-writer isolation, crash durability or a proof
that all possible resource leaks are absent. It does not address Spark's socket
ResourceWarnings.

## Cost, Failure And Recovery

Cost is temporary standard-library I/O in existing portable CI, with fixtures
under 132 KiB each. No dependency, schedule, compute SKU or job-budget increase.
Failures identify the violated file-boundary contract; fix a demonstrated defect
under a separate brief rather than weaken the regression. Revert these two files
to undo this coverage increment. No user/source files are mutated by the tests;
all mutations occur in owned temporary directories. Data/Delta/checkpoint/ACL
recovery and RTO/RPO are N/A.

## Validation And Review

Run focused tests and representative guard-removal controls locally, then full
exact-index acceptance and generated review evidence in GitHub CI on the final
combined candidate. Local Python is 3.13; CI remains pinned to Python 3.11. The
local tree is a hash-checked source subset, not a full upstream checkout.

A test-only read-only review is sufficient for this increment because it changes
no executable product or contract behavior. The combined PR's independent
correctness/adversarial reviews and exact human acceptance nevertheless remain
pending. Author mutation probes are not independent review or exhaustive testing.
