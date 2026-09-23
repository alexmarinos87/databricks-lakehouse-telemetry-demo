# Change Brief: Validate repository read budgets before input access

## Problem

On `a85d66f`, the read helper checks only whether limits are <= 0.
NaN and positive infinity supplied as `max_files` bypass the count guard and
consume all supplied paths. String limits escape as TypeError. Reproduction used
real temporary files and the exact original Git blob, not an external service.

## Acceptance Criteria

- All three limits require positive built-in integers before root lookup or path
  iteration; invalid configuration raises `repository_read_limit_invalid` without
  exposing supplied values.
- Reject booleans, floats (including integral floats, NaN and infinities), strings,
  None, Decimal/Fraction instances and int subclasses. Do not silently coerce.
- Preserve defaults, valid integer limits, exact byte/hash snapshots, zero-byte
  files, duplicate-path accounting, one-over-budget termination and rechecks.
- Demonstrate original failures, corrected passing tests and isolated negative
  controls; execute complete repository acceptance and Spark in existing CI.

## Architecture Boundaries And Non-Goals

Change only `src/lakehouse_demo/repository_files.py`,
`tests/test_repository_read_limits.py` and this brief. The implementation changes
only limit validation and its docstring. Signatures and existing error category
remain unchanged. The supported limit domain is now explicit: arbitrary numeric
objects and int subclasses are outside it. Callers using these must deliberately
supply valid built-in integers; the helper performs no lossy conversion.

No new upper ceiling for valid integer budgets, network/dependency changes,
fixture edits, transformation, schema, timestamp or monetary policy, workflow
change, publication change or concurrent-writer sandbox. Snapshot-verifier input
materialization and caller-controlled parent-directory assumptions are unchanged.

## Data, State, Security And Cost

The helper reads bounded caller-selected local files; it performs no writes.
Invalid configuration now fails before filesystem access. No business grain,
keys, null rules, replay, checkpoint, backfill, Delta or permission state changes.
No credentials, identities or external services. Validation checks three values
once per call. Defaults and CI resource ceilings stay unchanged; no scheduled
runs, compute SKU or storage growth. Tests mutate only owned temporary files.

## Failure And Recovery

Invalid limits have one existing sanitized public category, not TypeError or an
accidental successful read. Valid-limit count/byte errors stay distinct. Revert
this commit to restore previous configuration handling, including the defect.
No data/Delta/checkpoint/ACL restore or RTO/RPO applies. Re-run local and CI checks
on any replacement candidate; green results do not grant merge authority.

## Validation And Review

Five standard-library methods execute the actual helper, including a 48-case
invalid-input matrix, real-file positive boundaries and snapshot rechecks. Root
lookup and iterable consumption are observed, not replaced with a fake reader.
Local evidence is a hash-checked Python 3.13 source subset; cloning fails DNS and
Docker is unavailable. Full exact-index acceptance, generated review evidence,
Spark and source-artifact verification must be observed in GitHub CI. Independent
correctness/adversarial reviews and explicit human acceptance remain pending.
