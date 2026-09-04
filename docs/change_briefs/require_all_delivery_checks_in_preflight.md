# Change Brief: Require both delivery contexts in external readiness

## Problem

The GitHub governance bootstrap and effective-state verifier now require both
`validate` and `Round-trip synthetic review evidence`, but the lightweight
preflight used before Databricks CLI installation still treated `validate` alone
as sufficient. That mismatch could allow an authenticated plan command to proceed
when artifact compatibility was green on the commit but not actually enforced by
branch protection.

## Acceptance Criteria

- [ ] External readiness uses the same ordered two-context contract as the
      bootstrap, effective-governance verifier and accepted-commit verifier.
- [ ] The branch endpoint must report both contexts as required.
- [ ] Each missing context produces its own stable blocker.
- [ ] Evidence exposes both booleans and an aggregate
      `all_required_status_contexts_active` value.
- [ ] The readiness summary names both controls.
- [ ] Existing current-commit, protection, OIDC, host, client-ID and secret
      boundaries remain unchanged.
- [ ] No Databricks command or GitHub mutation is introduced.

## Non-Goals

- Inspecting current check-run conclusions; that belongs to
  `verify_main_check_runs.py`.
- Wiring the accepted-main check verifier into the plan workflow.
- Applying branch protection or environment configuration.
- Authenticating to Databricks or creating a plan.

## Architecture Boundaries

- Components and files allowed to change:
  - `scripts/check_external_readiness.py`
  - `tests/test_check_external_readiness.py`
  - this change brief
- The preflight remains one GET request to the accepted branch endpoint.
- The existing evidence schema version remains `1`; fields are additive.
- Provider mutation and raw provider diagnostics remain prohibited.

## Data, State And Side Effects

- GitHub read: unchanged branch endpoint.
- New interpretation: both reviewed status contexts must be present.
- Local evidence: existing JSON and Markdown files with additive booleans.
- Data models, tables, checkpoints, replay and schema evolution: N/A.
- Idempotency: repeated reads are safe.
- Partial failure: all independent blockers are retained in deterministic order.

## Security, Permissions And Cost

- Identity and token handling are unchanged.
- No new permission is needed in this source-only increment.
- Tokens, raw Databricks configuration and provider diagnostics remain excluded.
- Cost remains one bounded GitHub REST read and two small local files.
- Databricks and compute cost: none.

## Failure And Recovery

- New blocker: `artifact_compatibility_check_is_not_required`.
- Existing blocker: `validate_check_is_not_required`.
- Either blocker prevents a `ready` result.
- Recovery: configure the reviewed contexts on protected `main`, then rerun the
  same preflight against the exact accepted commit.
- Rollback: revert the candidate; no external state requires restoration.

## Validation Plan

- Ready state with both contexts.
- Independent absence of each required context.
- Combined failure with stale, unprotected and missing-context states.
- Cross-module required-context equality with the accepted-main verifier.
- Existing provenance, OIDC, configuration, sanitization and symlink cases.
- Repository-wide CI, artifact compatibility and generated review package.
- No authenticated Databricks or branch-protection apply run in this candidate.
