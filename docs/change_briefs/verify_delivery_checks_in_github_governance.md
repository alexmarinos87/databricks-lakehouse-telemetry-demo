# Change Brief: Verify the exact required delivery-check set

## Problem

The governance bootstrap can declare both accepted delivery checks, but the
independent effective-state verifier currently treats `validate` alone as
sufficient. That creates a false-closure path: live branch protection could omit
the artifact compatibility gate, or include an unreviewed extra required context,
while verification still reports success.

## Acceptance Criteria

- [ ] Effective `main` protection is accepted only when its normalized required
      status-context set exactly equals the two reviewed delivery checks.
- [ ] Missing `validate`, missing artifact compatibility, and any extra context
      produce stable, sanitized drift findings.
- [ ] Evidence records expected and actual contexts, exact-match status, and
      individual gate booleans without provider diagnostics or credentials.
- [ ] Existing repository, pull-request, environment, variable, secret and
      identity-separation checks remain unchanged.
- [ ] The external bootstrap runbook names both checks and the exact-set rule.

## Non-Goals

- Applying or changing live GitHub settings.
- Querying current commit check-run conclusions.
- Changing the plan-only command workflow.
- Authenticating to or mutating Databricks.

## Architecture Boundaries

- Components and files allowed to change:
  - `scripts/verify_github_governance.py`
  - `tests/test_verify_github_governance.py`
  - `tests/test_external_bootstrap_contract.py`
  - `docs/external_bootstrap.md`
  - this change brief
- The verifier remains an independent read-only client using GET requests only.
- Context normalization continues to support both legacy `contexts` and modern
  `checks[].context` response shapes.

## Data, State And Side Effects

- Inputs: ignored bootstrap configuration plus live GitHub repository,
  branch-protection and environment reads.
- Outputs: sanitized JSON and Markdown verification evidence.
- Table grain, keys, null rules, late data, schema evolution and deduplication:
  N/A.
- Read/write behaviour: GitHub reads and local evidence writes only.
- Idempotency and retry: repeated verification is safe; provider failure remains
  a bounded non-zero result with no raw response persisted.
- Migration requirement: rerun verification after the dependent desired-state
  policy has been applied.

## Security, Permissions And Cost

- Identity: the existing one-time `GITHUB_ADMIN_TOKEN`, used for administrative
  reads only by this verifier.
- Least privilege: repository settings, branch protection, environment variables
  and environment secret-name read access; no write method is introduced.
- Sensitive values: evidence contains SHA-256 fingerprints and stable categories,
  not token values, workspace hosts, client IDs, runtime IDs or secret values.
- Cost: bounded GitHub REST reads and local files only; no compute, Databricks or
  storage-service cost change.

## Failure And Recovery

- Missing or additional contexts block verification with
  `required_status_contexts_drift`.
- Missing individual accepted gates also retain specific categories for direct
  operator diagnosis.
- A malformed or unavailable provider response fails closed through the existing
  sanitized request-failure path.
- Recovery is to repair the named workflow or reapply the reviewed exact
  protection policy, then rerun the verifier. Do not weaken the expected set to
  make evidence pass.
- Rollback is a source revert followed, where live policy was changed, by an
  explicit reviewed reapplication and independent readback.

## Validation Plan

- Automated checks:
  - exact accepted state;
  - missing artifact gate;
  - unexpected required context;
  - existing repository, environment, redaction and provider-failure contracts;
  - repository-wide CI and artifact compatibility.
- Manual inspection:
  - expected tuple;
  - normalized set comparison;
  - finding categories and evidence fields;
  - GET-only client boundary.
- Checks outside this candidate:
  - live verification against applied production repository settings;
  - current commit check-run success verification.
