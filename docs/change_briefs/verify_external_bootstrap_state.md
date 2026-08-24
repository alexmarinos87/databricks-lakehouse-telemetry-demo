# Change Brief: Verify External Bootstrap State Before Planning

## Problem

The repository contains dry-run-first GitHub governance and Databricks
federation bootstrap scripts, but their effective-state checks are incomplete.

The GitHub apply path previously confirmed only that `main` reported
`protected: true` and that non-squash merge methods were disabled. It did not
prove the required status check, strict branch freshness, administrator
enforcement, review settings, linear history, conversation resolution,
environment branch restrictions, configured variable values, or the absence of
a static client secret.

The Databricks apply path previously trusted the configured relationship between
a numeric service-principal ID and an application ID. It also treated a create
response as sufficient without listing the new policy back.

These gaps make a bootstrap command look stronger than the evidence it
actually produces.

## Outcome

- Add an explicit read-only `--verify` mode to both bootstrap scripts.
- Extend the ignored GitHub config with explicit environment reviewer and
  self-review policy, including mandatory protected production approval.
- Make GitHub apply perform the same exact read-back verification after writes.
- Make Databricks apply verify numeric-to-application identity before policy
  creation and list every policy back after creation.
- Produce sanitized machine-readable results containing fingerprints rather
  than raw external identifiers or hosts.
- Fail closed on missing, duplicated, conflicting, unreadable, or misplaced
  controls.
- Document how to retain verification hashes without committing bootstrap
  configuration or sensitive provider output.

## Acceptance Criteria

### GitHub

- Repository settings require squash-only delivery and branch cleanup.
- `main` reports active protection.
- Protection requires a current `validate` check.
- Administrator enforcement, stale-review dismissal, the configured approval
  count, linear history, and conversation resolution are active.
- Force pushes and branch deletion are disabled.
- `dev-plan`, `prod-plan`, `dev`, and `prod` use only explicit `main`
  deployment policies.
- Plan environments have no required reviewer.
- Production has at least one configured user or team reviewer and prevents
  self-review; development reviewer protection is exact when configured.
- `DATABRICKS_HOST` and `DATABRICKS_CLIENT_ID` exactly match the ignored local
  config.
- `DATABRICKS_CLIENT_SECRET` is absent from both environment variables and
  environment secrets.
- Verify mode issues only `GET` requests.

### Databricks

- Every configured numeric service-principal ID resolves to the configured
  application ID and an active principal.
- Each expected GitHub environment subject exists exactly once.
- The subject is attached to the intended configured principal.
- Issuer, audience, and subject match the exact policy.
- Apply mode re-lists created policies before reporting success.
- Verify mode issues only service-principal `get` and policy `list` commands.

### Evidence

- Unit tests cover exact state, missing controls, drift, duplicate or misplaced
  subjects, identity mismatch, read-back after create, and output redaction.
- The runbook distinguishes dry run, apply, verification, independent review,
  and authenticated plan evidence.
- No test or source result is represented as proof that current external
  settings are active.

## Non-Goals

- This increment does not apply GitHub settings.
- It does not create or alter Databricks federation policies.
- It does not create service principals or grant workspace, catalog, schema,
  table, volume, job, pipeline, warehouse, query, or account permissions.
- It does not add a static client secret, personal access token, or token
  command-line argument.
- It does not run a Databricks bundle plan or deployment.
- It does not close issue #44, G13, or G14.

## Security Boundary

The ignored config remains the comparison source for external identifiers. Raw
values are used only in local process memory and provider requests. Normal
script output contains fingerprints and status fields.

Verification errors identify the failed control but do not include the expected
or observed host, client ID, account ID, numeric principal ID, application ID,
token, or provider diagnostic body.

A successful verification proves only what the authenticated read APIs return
at that moment. Independent branch or ruleset evidence and a successful
protected-main OIDC plan are still required.

## Failure And Recovery

- Missing GitHub token: stop before any provider request.
- Missing or malformed protection state: stop and report the control category.
- Environment reviewer or branch-policy drift: stop without silently
  broadening deployment access.
- Environment value drift: stop without printing either value.
- Static client secret detected: stop; remove it through an approved
  administrator action and verify again.
- Numeric/application identity mismatch: stop before policy creation.
- Subject attached to another configured principal: stop without moving or
  deleting the policy automatically.
- Missing policy in verify mode: stop; rerun reviewed apply only after examining
  current state.
- Create succeeds but read-back fails: treat the bootstrap as incomplete,
  inspect provider state, and do not proceed to the plan command.

## Evidence Boundary

Repository CI can prove command construction, response normalization,
fail-closed decisions, read-only verify behavior, and redaction. It cannot prove
that GitHub settings, environment values, Databricks principals, or federation
policies are currently configured. Those remain issue #44 evidence.
