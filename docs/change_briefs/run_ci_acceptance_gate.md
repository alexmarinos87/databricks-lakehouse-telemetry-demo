# Change Brief: Execute the exact-index acceptance gate in CI

## Problem

The source-evidence stack has portable Docker checks but repeatedly records the
required `scripts/run_acceptance_checks.sh` as unrun. GitHub's complete checkout
can execute it even when a local authoring environment cannot clone the repository.
This closes an execution-evidence gap in #144; it does not add a new policy layer.

## Acceptance Criteria

- The existing acceptance script executes after Docker validation and before the
  review package in the validate job, with no failure suppression.
- Its base is the event's immutable PR base SHA or push-before SHA, supplied via
  environment rather than interpolated shell code. Missing bases fail closed.
- A failed acceptance step fails validate and blocks dependent artifact publication.
- Tests exercise the actual shell script in temporary Git repositories for success,
  check failures, unavailable bases, exact-index isolation and whitespace rejection.
- Actual repository acceptance, full tests, review generation and artifact publication
  are observed in CI on the updated head and reported separately from local tests.

## Non-Goals

No acceptance-script rewrite, changed test semantics, new dependency or action,
permission expansion, trigger change, merge, deployment or Databricks operation.
This does not complete independent reviews or grant human acceptance.

## Architecture Boundaries

Change CI YAML; add one focused test file and this brief. Keep Docker validation,
review-package failure reporting and the needs: validate publication edge intact.
The host-runner acceptance pass complements the pinned Python 3.11 Docker pass.
The shell tests stub the two check entrypoints to test orchestration without
recursively invoking the repository suite; CI invokes the real entrypoints.

## Data, State And Side Effects

Read the checkout/index and export its tracked candidate to a temporary directory.
The existing script owns cleanup of that temporary directory. Tests use disposable
local Git repositories and do not touch caller files. No data, table, schema,
checkpoint, replay or permission migration. Existing artifacts and their format
are unchanged. Candidate source files are not rewritten by acceptance.

## Security, Permissions And Cost

Retain contents: read, non-persistent checkout credentials, existing action pins
and the ten-minute validation timeout. This adds one bounded standard-library
suite pass per CI run; no external service or Databricks compute. Event commit
values are used as environment data. A first push with an all-zero before SHA,
or an unavailable historical base, fails closed rather than skipping the diff.

## Failure And Recovery

Failures prevent a successful validate result and therefore publication. Existing
PR review-package steps still report failures when the run is not cancelled.
Correct the candidate/base availability and rerun. Rollback removes this invocation,
tests and brief, but restores the missing acceptance evidence; it is not equivalent
validation. Delta recovery, checkpoint/ACL rollback and RTO/RPO are not applicable.

## Validation Plan

Run seven focused tests locally, syntax and whitespace checks, then observe the
actual script in current-head GitHub CI, including the separate Git-index and
exported-tree checks. Do not describe stubbed shell tests as full local acceptance.
Independent correctness/adversarial review and human acceptance remain pending.
