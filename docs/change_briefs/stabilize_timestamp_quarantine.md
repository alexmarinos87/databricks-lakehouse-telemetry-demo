# Change Brief: Quarantine malformed timestamps regardless of ANSI mode

## Problem

The original Silver conversion uses `F.to_timestamp` before required-key
validation. Invalid timestamps may therefore throw with ANSI enabled instead of
entering quarantine. Source inspection and Apache Spark's documented null-on-error
`try_to_timestamp` contract motivate the fix; an actual original-expression failure
is also required in the runtime regression, not inferred from portable tests.

## Acceptance Criteria

- Invalid, impossible-calendar, empty, whitespace and null timestamps reach the
  existing invalid-key quarantine under ANSI true and false.
- Valid values, timestamp result types (LTZ and NTZ), raw timestamp evidence,
  fingerprints, conflict classification and latest identical replay are preserved.
- Production code does not modify caller configuration or catch arbitrary errors.
- Five focused real-Spark tests and the inherited runtime suite pass; portable
  acceptance and the review package are recorded separately.

## Non-Goals

No stricter timestamp grammar, timezone policy, numeric-cast repair, numeric quality
policy, schema migration, monetary change, checkpoint operation, notebook write,
source-fixture edit or runtime/dependency upgrade. Other malformed numeric casts
remain ANSI-sensitive; this is not universal malformed-row quarantine. NaN,
infinite and out-of-range metrics require a separately scoped data-quality policy.

## Architecture Boundaries

One conversion in `spark_medallion.py`, a focused runtime test file and this brief.
Use `try_to_timestamp`, available in the pinned PySpark 3.5.0. Preserve the existing
source-payload-before-casting order and every subsequent transformation. Do not
extend the already-large portfolio integration #147; branch from main and retain
separate commits for runtime-runner work.

## Data, State And Side Effects

Input remains source-shaped Bronze; outputs remain Silver and quarantine DataFrames.
Keys, replay identity and exact-payload conflicts are unchanged. The intended change
is that invalid timestamps become existing quarantine rows instead of aborting an
ANSI-enabled materialization. No persistent data, source, table, schema, checkpoint,
manifest, view, permission or external service is modified by this work.

## Security, Permissions And Cost

No credential, provider session, grant, deployment environment or schedule. Five
small synthetic tests execute in the existing two-CPU/4-GiB local Spark job, within
its twenty-minute limit. No production runtime/SKU change. Daily frequency follows
existing relevant PR/push events, not a new schedule. Caches and Spark session have
cleanup handlers. Existing warnings remain visible.

## Failure And Recovery

Missing input columns, numeric conversion errors and unexpected programming errors
retain their existing behavior. Correct invalid source timestamps and replay under
the established immutable-input procedure; do not erase quarantine evidence.
Reverting this conversion restores ANSI-sensitive failure, not equivalent safety.
This code-only candidate requires no Delta restore, migration, checkpoint reset or
permission rollback. Runtime recovery in a workspace remains untested; RTO/RPO N/A.

## Validation Plan

Run a reference `to_timestamp` expression under ANSI and require CAST_INVALID_INPUT;
then exercise the real Silver builder under both modes. Check full quarantine
materialization, original raw timestamp preservation, valid-value/type parity,
conflicting valid/invalid payloads and latest identical delivery. Restore test
configuration in finally blocks. Run actual acceptance and all Spark tests remotely.
The local authoring environment has no PySpark/Docker and cannot clone GitHub;
local compilation is not runtime evidence. Independent correctness/adversarial
reviews and exact human acceptance remain pending. No merge is authorized.

Reference: https://spark.apache.org/docs/3.5.9/api/python/reference/pyspark.sql/api/pyspark.sql.functions.try_to_timestamp.html
