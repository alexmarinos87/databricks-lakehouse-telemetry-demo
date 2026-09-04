# Change Brief: Verify required checks on the accepted `main` commit

## Problem

Branch protection can require the reviewed delivery-check names, but that desired
and effective policy does not prove that those checks actually completed
successfully on the exact commit selected for an authenticated Databricks plan.
A stale, missing, failed, ambiguous, or differently sourced check run must not be
treated as accepted-main evidence.

## Acceptance Criteria

- [ ] One bounded, read-only GitHub Checks request inspects the exact accepted
      commit with `filter=latest` and a fixed page limit.
- [ ] Both `validate` and `Round-trip synthetic review evidence` must each resolve
      to exactly one completed, successful check run from the GitHub Actions app.
- [ ] Missing, ambiguous, incomplete, unsuccessful, wrong-app and commit-mismatched
      checks produce stable blocker categories.
- [ ] Truncated, oversized, malformed or unavailable provider responses fail
      closed without persisting raw diagnostics.
- [ ] JSON and Markdown evidence contain only the accepted commit, required check
      names, bounded states, app slug and blocker categories.
- [ ] The required check tuple remains aligned with both governance-policy modules.

## Non-Goals

- Applying GitHub branch protection or repository settings.
- Changing the comment-triggered Databricks plan workflow.
- Authenticating to Databricks, creating a plan, deploying or mutating data.
- Treating automated evidence as human acceptance.

## Architecture Boundaries

- Components and files allowed to change:
  - `scripts/verify_main_check_runs.py`
  - `tests/test_verify_main_check_runs.py`
  - `docs/accepted_main_check_evidence.md`
  - this change brief
- The verifier may contact only `https://api.github.com`.
- The verifier uses GET only and accepts no token command-line argument.
- The existing bootstrap and effective-governance verifiers remain unchanged.

## Data, State And Side Effects

- Input: repository name, accepted commit SHA and short-lived `GITHUB_TOKEN`.
- Provider read: one latest-check inventory for that exact commit.
- Local output:
  - `main-check-runs-verification.json`
  - `main-check-runs-verification.md`
- Table grain, keys, null handling, late data, schema evolution, checkpoints and
  replay: N/A.
- Idempotency: repeated reads for the same immutable commit are safe.
- Partial failure: bounded failure evidence is written when the output directory
  is available; no provider response body or check output is retained.

## Security, Permissions And Cost

- Identity: the workflow-scoped GitHub token, read from the environment only.
- Least privilege: `checks: read` will be required by the later workflow
  integration; no contents or administration write is needed.
- Secret boundary: token values, check output, annotations, URLs and provider
  diagnostics are excluded from evidence.
- Cost: one bounded GitHub REST read and two small local evidence files.
- Databricks, compute, storage and external-service cost: none.

## Failure And Recovery

- Expected blockers:
  - required check missing or ambiguous;
  - check not completed or not successful;
  - check reported by an unexpected app;
  - check bound to a different commit.
- Expected verifier failures:
  - missing token;
  - unapproved API URL;
  - request failure;
  - oversized, malformed or truncated inventory;
  - unsafe output directory.
- Recovery: repair or rerun the named accepted-main workflow, then rerun this
  verifier against the unchanged accepted commit. Do not weaken the required set.
- Rollback: revert the candidate; no external state requires restoration.
- RTO/RPO, Delta restore, checkpoints and ACL recovery: N/A.

## Validation Plan

- Automated checks:
  - accepted two-check inventory;
  - missing, failed, in-progress, ambiguous, wrong-app and wrong-commit cases;
  - truncated inventory and sanitized provider failure;
  - API allow-list, missing token and symlink output rejection;
  - source-level GET-only and cross-module tuple-alignment contracts;
  - repository-wide CI and artifact compatibility.
- Manual inspection:
  - request construction and response bounds;
  - check matching and blocker ordering;
  - persisted evidence fields;
  - token and provider-diagnostic exclusion.
- Checks outside this candidate:
  - live workflow integration;
  - Databricks authentication or plan collection;
  - applying and verifying branch protection.
