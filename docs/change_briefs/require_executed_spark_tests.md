# Change Brief: Require complete executed Spark test evidence

## Problem

The runtime shell delegates to unittest discovery without requiring complete
execution. Local Python 3.13.5 reproductions returned exit 0 for a skipped suite
and an expected-failure suite. Its empty CLI suite returned exit 5, not 0: do not
claim that local interpreter reproduced the older empty-suite behavior. The
repository pins Python 3.11, and the candidate explicitly guards zero discovery
rather than depending on interpreter-specific CLI behavior.

## Acceptance Criteria

- Runtime discovery uses the same directory, pattern and verbose unittest runner.
- Preserve the original shell discovery flags, parse them, and reject unsupported
  directory/pattern overrides instead of silently ignoring them.
- Empty/missing discovery, test/import errors, assertions and unexpected successes
  return nonzero. Skips and expected failures cannot count as complete evidence.
- Normal passing suites still return 0. Do not suppress warnings or diagnostics.
- Actual launcher subprocess tests exercise empty, partial, successful and failed
  suites without importing or installing Spark in the portable test environment.
- Actual exact-index acceptance runs against the immutable event base before the
  Spark image build. Failures prevent the runtime step. Pins and resource limits
  remain unchanged.

## Non-Goals

No test-count hardcoding, suite splitting, warning suppression, dependency upgrade,
performance claim, workspace operation, new artifact format, source-only evidence
flag change, permissions expansion or automatic merge. This is not protection
against malicious tests, arbitrary interpreter exits or a removed individual test;
review still owns suite completeness.

## Architecture Boundaries

Add `scripts/run_spark_runtime_checks.py`; route the existing shell to it. Modify
only the existing Spark workflow's watched runner/test paths, full-history checkout
and acceptance invocation. Add seventeen portable tests and this brief. Together with
the separately committed timestamp fix this remains a small main-targeted runtime
candidate, not an extension of #147's large source-evidence integration.

## Data, State And Side Effects

Read repository tests and execute their existing synthetic local-Spark operations.
Discovery occurs once per run. No table, checkpoint, schema, business-data, manifest,
query, grant or source fixture changes. Test imports and execution remain trusted
code. Portable regressions copy only the two launcher scripts into temporary
projects and run small standard-library child suites. Existing test diagnostics
remain available; skip/expected-failure summaries additionally receive an explicit
incomplete-evidence error and exit 1.

## Security, Permissions And Cost

Retain contents: read, non-persistent checkout credentials, exact action/image/
package pins, per-ref cancellation, two CPUs, 4 GiB and twenty-minute job timeout.
No OIDC, secrets mapping, environment, privileged event or schedule. Full Git history
and one portable acceptance pass add runner work before Spark, within the same job
budget. No production SKU/runtime, persistent storage or daily scheduled cost.

## Failure And Recovery

Restore missing tests/dependencies or fix actual failures and rerun; do not remove
assertions, add skips or mark expected failures to regain a green runtime check.
An unavailable immutable base fails acceptance rather than falling back to a moving
branch. Reverting this runner permits skipped/expected-failure successes again.
No Delta versions, data restores, checkpoint resets, permission rollback or RTO/RPO
apply to this code-only candidate. Full runtime evidence must be reacquired after
changes. Existing upstream socket warnings remain unresolved and visible.

## Validation Plan

Run seventeen portable tests through real launcher subprocesses and verify Python
3.11 syntax compatibility, Bash syntax, YAML and whitespace locally. Observe the
full actual acceptance script and real Spark suite in GitHub on the exact final
candidate. The local environment lacks Docker/PySpark and cannot resolve GitHub
for cloning: focused subprocess tests are not full local repository acceptance.
Inspect discovery count, result handling, shell exit propagation, exact BASE_REF
and acceptance-before-image ordering. Independent correctness/adversarial review
and exact human acceptance remain pending. No merge/deployment is authorized.

## Prospective Integration Correction

A local source-subset check of the prospective #147/#148 combination found that
#147's existing wiring test requires the original shell discovery flags. The
corrective commit retains those flags and genuinely parses/uses them in the new
runner. Only the original directory/pattern values are allowed; this does not add
generic arbitrary test selection. Two additional subprocess tests check canonical
arguments and rejected overrides. The combined three-test wiring subset must pass;
it is not a full merged-repository acceptance or combined Spark run. Neither
existing PR branch is edited or merged by this compatibility correction.
