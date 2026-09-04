# Change Brief: Require both accepted delivery checks on `main`

## Problem

The repository now has two independent pull-request checks that protect different
failure classes:

- `validate` proves the repository contracts and standard-library test suite;
- `Round-trip synthetic review evidence` exercises the pinned artifact upload and
  download path used by reviewed Databricks plans.

The GitHub-governance bootstrap still configures only `validate` as a required
status context. A protected branch created from that source policy could therefore
accept a change when the artifact-compatibility gate is absent or failing.

## Acceptance Criteria

- [ ] The generated `main` protection payload is strict and requires both accepted
      check contexts in deterministic order.
- [ ] Dry-run and apply summaries expose the complete non-sensitive context list.
- [ ] Tests inspect the exact protection request and reject duplicate or missing
      required contexts.
- [ ] Existing pull-request, linear-history, administrator-enforcement,
      conversation-resolution, force-push and deletion controls remain unchanged.

## Non-Goals

- Applying GitHub repository settings.
- Changing the independent effective-state verifier.
- Running an authenticated Databricks plan or deployment.
- Adding approval requirements beyond the existing configurable zero-or-one
  maintainer policy.

## Architecture Boundaries

- Components and files allowed to change:
  - `scripts/bootstrap_github_governance.py`
  - `tests/test_bootstrap_github_governance.py`
  - this change brief
- Existing environment creation, variable handling and redaction contracts must
  remain compatible.
- No GitHub API endpoint, token source or write operation is added.

## Data, State And Side Effects

- Inputs and outputs: ignored bootstrap JSON in; sanitized dry-run/apply summary
  and GitHub settings writes out.
- Table grain, keys and null rules: N/A.
- Late-data, schema-evolution and deduplication rules: N/A.
- Checkpoint, replay and source-file identity strategy: N/A.
- Read/write behaviour: source execution remains dry-run by default; `--apply`
  continues to write repository settings and four environments.
- Idempotency, retry and partial-failure behaviour: unchanged from the existing
  bootstrap. The protection PUT remains a complete desired-state replacement.
- Backfill or migration requirements: an operator must rerun the reviewed
  bootstrap with settings access to activate the additional required check.

## Security, Permissions And Cost

- Identities, grants or secrets involved: the optional one-time
  `GITHUB_ADMIN_TOKEN`; its handling is unchanged.
- Least-privilege expectation: repository administration, environment and
  Actions-variable write access only for an explicit apply.
- Compute, storage or external-service cost impact: N/A for source validation;
  one additional status context can delay or block future merges when its
  workflow does not pass.
- Expected runs/day, compute SKU/runtime delta, storage growth and cost ceiling:
  N/A.

## Failure And Recovery

- Expected failure modes: a repository without the named check will block merges
  after the policy is applied; a failed bootstrap request remains fail-closed.
- Detection and observability: dry-run output lists both contexts; the follow-up
  verifier stack will independently compare effective state.
- Rollback or forward-recovery procedure: restore the prior reviewed bootstrap
  commit and explicitly reapply its policy, or repair the named workflow/check.
- Data recovery or reconciliation procedure: N/A.
- Pre-change Delta versions/snapshots and restore order: N/A.
- Checkpoint and permission/query-ACL rollback: N/A.
- Recovery validation and required RTO/RPO: re-query branch protection and
  confirm the intended exact context set before allowing merge activity.

## Validation Plan

- Automated checks:
  - repository CI and acceptance checks;
  - exact payload, request and sanitized-summary unit tests.
- Runtime/integration checks: none; no repository settings are applied by this
  candidate.
- Manual inspection points:
  - the required-context tuple;
  - the complete branch-protection payload;
  - the exact PUT payload asserted by the test.
- Checks that cannot run in the current environment:
  - applying and independently verifying live GitHub branch protection.
