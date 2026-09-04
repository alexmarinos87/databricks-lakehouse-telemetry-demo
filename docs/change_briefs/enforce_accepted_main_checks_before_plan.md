# Change Brief: Enforce accepted-main checks before Databricks planning

## Problem

The repository can now define, verify and inspect the two required delivery
checks, but the owner-triggered Databricks plan command does not yet consume
check-run evidence for the exact accepted commit. Without workflow enforcement,
a standalone verifier can be correct while the authenticated CLI path still
depends only on external-readiness evidence.

## Acceptance Criteria

- [ ] The plan job receives only the additional `checks: read` permission.
- [ ] External readiness and accepted-main check verification both run against the
      exact checkout recorded in `accepted-main-sha.txt`.
- [ ] Both gates execute before Databricks CLI installation and retain independent
      sanitized evidence.
- [ ] CLI installation and plan capture require readiness `ready` and check
      verification `verified`.
- [ ] A blocked, failed, missing or malformed gate skips all Databricks commands,
      fails the job and still retains available evidence.
- [ ] The issue comment reports both context-enforcement and check-run outcomes
      without raw provider diagnostics.
- [ ] Existing owner-only, issue #44, `dev-plan`, GitHub OIDC and plan-only
      boundaries remain unchanged.

## Non-Goals

- Applying branch protection, environments or repository settings.
- Running the command or authenticating to Databricks in this pull request.
- Deploying, uploading data, executing SQL or mutating permissions.
- Supporting production plan collection.
- Replacing human review or human acceptance.

This workflow gate does not deploy, apply changes, upload data, execute SQL, or
mutate permissions.

## Architecture Boundaries

- Components and files allowed to change:
  - `.github/workflows/plan-evidence-command.yml`
  - `tests/test_plan_main_check_gate.py`
  - `docs/accepted_main_check_evidence.md`
  - this change brief
- Reuse `scripts/check_external_readiness.py` and
  `scripts/verify_main_check_runs.py` without modification.
- The new provider access is one GitHub Checks GET using the workflow token.
- The existing immutable checkout, CLI setup and artifact actions remain pinned.

## Data, State And Side Effects

- GitHub reads:
  - existing branch-state readiness request;
  - one exact-commit latest-check inventory.
- Local and artifact state:
  - readiness JSON and Markdown;
  - accepted-main check JSON and Markdown;
  - structured Databricks plan evidence only when both gates pass;
  - one 14-day GitHub Actions artifact.
- Issue side effect: the existing sanitized issue #44 comment gains bounded
  check-policy and check-run fields.
- No data tables, checkpoints, schemas or Databricks resources are changed.

## Security, Permissions And Cost

- New permission: `checks: read`; no checks, Actions or contents write.
- Existing permissions remain `contents: read`, `issues: write` and
  `id-token: write`.
- The short-lived GitHub token is supplied through the environment only.
- Static Databricks client secrets remain prohibited.
- Cost: one additional bounded GitHub REST read and two small evidence files.
- Databricks cost occurs only in a separately accepted live invocation after all
  gates pass; this candidate performs no invocation.

## Failure And Recovery

- Any gate outcome other than `ready`/`verified` blocks CLI installation.
- Missing or malformed evidence is reported as unavailable and blocks.
- Evidence upload and sanitized issue reporting still run after gate failure.
- Recovery:
  1. fix branch protection, the accepted-main checks or environment OIDC;
  2. rerun the reviewed main checks when necessary;
  3. issue the exact owner command again against the current accepted commit.
- Rollback: revert the workflow candidate; no external state needs restoration.
- Delta versions, data recovery, checkpoints and ACL restoration: N/A.

## Validation Plan

- Source contract proving `checks: read` is the only new permission.
- Step-order contract: accepted SHA, readiness, check verification, CLI, plan.
- Exact dual-gate condition on both CLI and plan steps.
- Failure-enforcement, evidence-retention and issue-comment contracts.
- Owner-only, plan-only, secretless and no-deploy regression checks.
- Repository-wide CI, artifact compatibility and generated review package.
- Correctness/maintainability and adversarial production review.
- Human acceptance before merge.
- No authenticated Databricks execution in this candidate.
