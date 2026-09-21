# Change Brief: Make standalone profiler failures safe for automation

## Problem

The standalone profiler invokes source discovery, validation and publication without
an exception boundary. Expected DatasetProfileError failures therefore escape as
tracebacks, including chained filesystem diagnostics and local paths. The existing
library exposes stable categories, but the command does not use them. An exact-blob
CLI reproduction with an injected chained filesystem failure confirmed the leak.
This is a CLI failure-path defect, not a demonstrated secret or customer-data leak.

## Acceptance Criteria

- Expected profile failures return exit 1 and exactly one JSON stderr record with
  status, category and structured detail codes; no stdout success or traceback.
- Ordinary discovery/filesystem path failures return a stable generic category,
  without copying exception messages, raw input, paths or chained diagnostics.
- Preserve successful JSON keys, default/explicit sources, no-argument invocation,
  validation, output names, replay grain, numerical policy and non-overwrite rules.
- Do not swallow interrupts or relabel unexpected RuntimeError programming failures.
- Prove the wrapper with injected failures and real subprocess integration tests,
  then run current-head CI, actual acceptance and artifact publication.

## Non-Goals

No library/schema/fixture change, new validation policy, directory-discovery rewrite,
new dependency, deployment, provider access, monetary policy or new evidence layer.
Successful output still includes the caller-selected output_dir for compatibility;
this correction concerns failure diagnostics, not all successful CLI fields.

## Architecture Boundaries

Only scripts/profile_synthetic_dataset.py, tests/test_profile_cli_errors.py and this
brief. Add an optional argv parameter for testing, retaining ordinary sys.argv use.
Catch DatasetProfileError and expected OSError/ValueError failures around discovery,
profiling and writing. The source library remains responsible for validation and
cleanup. Do not serialize exception text or its cause.

## Data, State And Side Effects

Input fixtures stay read-only; aggregation grain and replay semantics are unchanged.
Successful invocation writes the existing two-file package into a new directory.
Failure never emits a success record. The inherited writer's conservative partial
cleanup and caller-controlled-parent assumptions still apply; uncertain partial
output can remain for inspection. No data, checkpoint or permission migration.
Tests write disposable directories only; the local injected test dependency is not
part of the repository change and is not full library validation evidence.

## Security, Permissions And Cost

No credentials, network calls, provider permissions or scheduling. Error codes come
from the existing trusted library; raw messages and filesystem causes are excluded.
Extra cost is negligible exception handling; hosted CI retains existing limits.
No Databricks or other cloud compute is started by this change.

## Failure And Recovery

Correct the input or output selection and rerun with a fresh output path. The new
failure JSON is deliberately different from an uncaught traceback; successful JSON
is compatible. Reverting only this wrapper fix restores traceback disclosure and
is not an equivalent diagnostic contract. Data recovery, RTO/RPO, Delta restore,
checkpoint reset and permission rollback are not applicable.

## Validation Plan And Review

Nine wrapper tests cover compatible success, explicit sources, all failure stages,
chained diagnostics, filesystem errors, programming errors, interrupts and sys.argv.
Five real subprocess cases cover missing roots, invalid headers, valid/repeated
output, numerical limits and symbolic links. The full upstream checkout is not
available locally; distinguish the nine injected local tests from real dependency
integration and the complete acceptance script observed in GitHub CI.

This is a corrective commit on a consolidated review branch inheriting #145 and its
predecessors, not a rewrite or acceptance of those PRs. Inspect main's exception
boundary, successful-output compatibility and real subprocess stderr assertions.
Independent correctness/adversarial review and human acceptance remain pending.
