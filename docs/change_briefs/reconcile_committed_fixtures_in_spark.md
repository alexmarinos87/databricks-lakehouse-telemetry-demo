# Change Brief: Reconcile committed source fixtures in actual Spark

## Problem

Source profiling and handcrafted Spark scenarios provide different evidence. The
existing Spark workflow ignores CSV fixture and source-profile dependency changes.
A green source snapshot is not proof that its fixture measures survive the real
Silver and governed Gold transformations. This is an execution-coverage gap,
not a demonstrated error in the existing fixture totals.

## Acceptance Criteria

- Load the actual sample and default discovered increments with the source profiler
  and construct source-shaped Bronze DataFrames with deterministic delivery lineage.
- Execute the shared Spark Silver functions and reconcile physical, unique, replay
  and quarantine counts, coverage, categories and operational totals.
- Execute governed Gold functions and reconcile duration, downtime and cost totals.
- Replay every fixture row with later lineage, shuffle partitions, and prove exact
  Silver business-row conservation plus unchanged Gold operational totals.
- Both PR and main-push path filters watch these fixtures and direct dependencies.
- Observe actual current-head Spark execution separately from portable acceptance
  and the source-only snapshot artifact. Do not edit the snapshot's runtime flags.

## Non-Goals

No transformation, table/schema, source fixture, numerical policy or dependency
change. No Auto Loader, checkpoint, Delta publication, Unity Catalog, workspace
login, bundle plan/apply, query execution or deployment. Exact fixture cost parity
is not a general Decimal-versus-Spark-double equality claim for arbitrary inputs.
No automatic approval, merge or runtime-upgrade authority is introduced.

## Architecture Boundaries

Four paths: new runtime test, existing Spark workflow path filters, new portable
wiring test and this brief. Existing runtime discovery, image and package pins,
CPU/memory/timeout limits, read-only permissions and concurrency remain unchanged.
The source profiler remains a source-only tool; runtime evidence belongs to the
separate Spark job. The tests use the same governed Gold wrapper as the notebook,
not a second implementation of transformation rules. Both profile and Spark row
counts are also checked against the committed 31/30/1 demonstration fixture.

## Data, State, Permissions And Cost

Read committed synthetic fixtures only. Work with in-memory local DataFrames,
explicit synthetic timestamps and fixture-relative lineage, never private paths
or customer data. Cached frames and the session have registered cleanup handlers,
including setup failure. No persistent table, checkpoint or catalog is created.
The existing job runs with two CPUs, 4 GiB and a twenty-minute timeout; the added
four tests and expanded relevant path filters add local hosted-runner work only.
No credentials, OIDC, deployment environment, schedule or new external service.
Package/image downloads remain the existing runtime job's dependencies.

## Failure, Recovery And Rollback

Source validation, count, category, payload, or aggregate mismatch fails a named
test. Runtime startup/dependency errors fail rather than becoming skipped proof.
The Spark job remains separate from source artifact publication; inspect both
results for acceptance. No successful source artifact claims Spark execution.
Correct the candidate and rerun. Reverting this four-path increment restores the
coverage gap but requires no table/schema/checkpoint/permission recovery. Delta
versions, RTO/RPO, migration and data backfill are not applicable.

## Validation And Review

Run portable wiring tests, compilation, Python 3.11 grammar and whitespace checks
locally. Observe full portable acceptance and the entire actual Spark suite in
GitHub CI on the exact candidate; report their counts separately. Local clone and
Spark are unavailable in the current authoring container, not inferred passes.
Inspect source/lineage construction, comparison grain, numeric scope and both
path-filter lists. Independent correctness/adversarial review and explicit human
acceptance remain pending for the integrated candidate. No merge is authorized.
